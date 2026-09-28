"""검색 뜻 분석의 계산: 기준 검색 순위 · 주제 무리 · 근접 중복 · 기준 검색 점수(KPI) · 제안 후보.

모두 numpy · scikit-learn 계산이고 DB를 모른다. 작업(semantic.py)이 벡터와 판정을 모아 넘기고,
계산이 길어 따로 된 스레드에서 부른다.

- 기준 검색: 질의 벡터로 코퍼스(휴지통 밖 문서) 전체의 코사인 유사도를 정확히 계산한다(묶음마다 행렬 곱).
  질의마다 상위 RANKING_TOP_K와, 판정이 있는 문서의 정확한 순위(1 + 더 가까운 문서 수)를 남긴다.
- 주제 무리: 문서 벡터를 KMeans로 TOPIC_CLUSTERS 무리로 나누고, 질의는 가장 가까운 무리 가운데로 보낸다.
  질의가 하나도 없는 무리는 질의 만들기가 채울 곳이다.
- 근접 중복: 이웃 NEAR_NEIGHBORS개 가운데 코사인 ≥ NEAR_DUPLICATE_SIMILARITY인 쌍. 질의 · 문서 모두 모든 쌍을 남긴다.
  문서 쌍은 거짓 오답의 원인이다.
- 기준 검색 점수: 학습에 쓰는 질의 전부의 Recall@10 · MRR@10 (train · valid · test로 나누지 않는다).
- 제안 후보: 거짓 오답(오답인데 정답 유사도의 margin배 넘게 가까움) · 빠진 정답(판정 없는 상위 문서) ·
  정답 의심(정답인데 SUSPECT_RANK_FROM위 밖). Jev에 물을 순서대로 놓는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from dent.system import neighbors

# 질의마다 남기는 기준 검색 상위 문서 수 (질의 패널의 판정 · 순위, Recall@10)
RANKING_TOP_K = 10

# 한 번에 유사도를 계산하는 질의 수. 256 × 문서 20만 × 4바이트 ≈ 200MB를 넘지 않게 한다.
RANKING_BLOCK_ROWS = 256

# 주제 무리 수와 씨앗 값 (같은 데이터면 같은 무리)
TOPIC_CLUSTERS = 8
TOPIC_RANDOM_STATE = 42
TOPIC_N_INIT = 4

# 근접 중복: 이웃 수와 유사도 경계 (정한 것: 0.95)
NEAR_NEIGHBORS = 10
NEAR_DUPLICATE_SIMILARITY = 0.95

# 정답 의심: 정답인데 이 순위 밖이면 의심한다(오답 찾기 범위의 끝과 같다)
SUSPECT_RANK_FROM = 100

# 빠진 정답: 판정 없는 상위 문서 가운데 질의마다 볼 최대 수
MISSING_PER_QUERY = 3

# 정답이 없는 질의는 판정 없는 상위 몇 개를 빠진 정답 후보로 본다
MISSING_TOP_FOR_UNANSWERED = 3

# 기준 검색 점수를 재는 상위 수
KPI_AT = 10


@dataclass(frozen=True)
class QueryFacts:
    """질의 한 건의 사실 (행 순서대로)."""

    # 학습에 쓰는지 (휴지통 밖 · 뺀 이유 없음)
    included: bool

    # 정답 문서의 자리 · 오답 문서의 자리 (문서 행 순서의 번호)
    positives: tuple[int, ...]
    negatives: tuple[int, ...]


@dataclass(frozen=True)
class RankedPair:
    """기준 검색의 한 줄: 질의 자리 · 문서 자리 · 순위 · 유사도."""

    query_index: int
    document_index: int
    rank: int
    similarity: float


@dataclass(frozen=True)
class Candidate:
    """제안 후보 하나: 종류 · 질의 자리 · 문서 자리 · 순위 · 유사도."""

    kind: str
    query_index: int
    document_index: int
    rank: int
    similarity: float


@dataclass(frozen=True)
class NearPair:
    """근접 중복 한 쌍 (행 순서의 자리, 작은 것이 앞)."""

    index_a: int
    index_b: int
    similarity: float


@dataclass
class Findings:
    """뜻 분석 계산의 결과."""

    # 기준 검색 줄들
    rankings: list[RankedPair] = field(default_factory=list)

    # 문서 · 질의의 주제 무리 번호 (행 순서대로)
    document_clusters: list[int] = field(default_factory=list)
    query_clusters: list[int] = field(default_factory=list)

    # 질의가 없는 무리 번호들 · 가장 큰 무리의 몫 · 무리 수
    empty_clusters: list[int] = field(default_factory=list)
    largest_share: float = 0.0
    cluster_count: int = 0

    # 근접 중복 쌍 (질의 · 문서)
    query_pairs: list[NearPair] = field(default_factory=list)
    document_pairs: list[NearPair] = field(default_factory=list)

    # 기준 검색 점수 {recall_at_10, mrr_at_10, evaluated}. 잴 질의가 없으면 None
    kpi: dict[str, float | int | str] | None = None

    # 제안 후보 (Jev에 물을 순서)
    candidates: list[Candidate] = field(default_factory=list)


def analyze(
    query_vectors: np.ndarray,
    document_vectors: np.ndarray,
    queries: Sequence[QueryFacts],
    *,
    margin: float,
) -> Findings:
    """질의 · 문서 벡터(행 순서)와 질의 사실로 뜻 분석을 계산한다."""
    unit_queries = _normalized(query_vectors)
    unit_documents = _normalized(document_vectors)
    findings = Findings()
    _cluster(findings, unit_queries, unit_documents)
    findings.query_pairs = _near_pairs(unit_queries)
    findings.document_pairs = _near_pairs(unit_documents)
    if len(unit_queries) and len(unit_documents):
        findings.rankings, best_positive = _rank(unit_queries, unit_documents, queries)
        findings.kpi = _kpi(findings.rankings, queries)
        findings.candidates = _candidates(findings.rankings, queries, best_positive, margin=margin)
    return findings


def _normalized(vectors: np.ndarray) -> np.ndarray:
    """길이를 1로 맞춘 float32 벡터 (코사인 유사도 = 내적)."""
    unit = np.asarray(vectors, dtype=np.float32)
    if unit.size == 0:
        # 질의가 없는 데이터셋(문서만): (0, 차원) 그대로 둔다.
        return unit
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)


def _cluster(findings: Findings, unit_queries: np.ndarray, unit_documents: np.ndarray) -> None:
    """문서를 KMeans로 무리 짓고, 질의는 가장 가까운 무리로 보낸다."""
    count = len(unit_documents)
    if count == 0:
        findings.query_clusters = [-1] * len(unit_queries)
        return
    clusters = min(TOPIC_CLUSTERS, count)
    # 쓸 때 불러온다(scikit-learn은 무겁다).
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=clusters, random_state=TOPIC_RANDOM_STATE, n_init=TOPIC_N_INIT)
    labels = model.fit_predict(unit_documents)
    findings.document_clusters = [int(label) for label in labels]
    findings.cluster_count = clusters
    sizes = np.bincount(labels, minlength=clusters)
    findings.largest_share = float(sizes.max() / count)
    if len(unit_queries):
        centers = _normalized(model.cluster_centers_)
        query_labels = np.argmax(unit_queries @ centers.T, axis=1)
        findings.query_clusters = [int(label) for label in query_labels]
        used = set(findings.query_clusters)
    else:
        used = set()
    findings.empty_clusters = [cluster for cluster in range(clusters) if cluster not in used]


def _near_pairs(unit: np.ndarray) -> list[NearPair]:
    """이웃 가운데 유사도 경계를 넘는 쌍."""
    if len(unit) < 2:
        return []
    found = neighbors.nearest_neighbors(unit, k=NEAR_NEIGHBORS)
    pairs: dict[tuple[int, int], float] = {}
    for index in range(len(unit)):
        for neighbor, similarity in zip(
            found.indices[index], found.similarities[index], strict=True
        ):
            if similarity < NEAR_DUPLICATE_SIMILARITY:
                break
            other = int(neighbor)
            key = (min(index, other), max(index, other))
            pairs[key] = max(pairs.get(key, 0.0), float(similarity))
    return [NearPair(index_a=a, index_b=b, similarity=s) for (a, b), s in sorted(pairs.items())]


def _rank(
    unit_queries: np.ndarray, unit_documents: np.ndarray, queries: Sequence[QueryFacts]
) -> tuple[list[RankedPair], dict[int, float]]:
    """질의마다 상위 RANKING_TOP_K와 판정 문서의 정확한 순위. (줄들, 질의 자리 → 가장 가까운 정답 유사도)."""
    top_k = min(RANKING_TOP_K, len(unit_documents))
    rows: list[RankedPair] = []
    best_positive: dict[int, float] = {}
    for start in range(0, len(unit_queries), RANKING_BLOCK_ROWS):
        end = min(start + RANKING_BLOCK_ROWS, len(unit_queries))
        block = unit_queries[start:end] @ unit_documents.T
        top = np.argpartition(-block, top_k - 1, axis=1)[:, :top_k]
        for offset, query_index in enumerate(range(start, end)):
            similarities = block[offset]
            order = top[offset][np.argsort(-similarities[top[offset]], kind="stable")]
            seen: set[int] = set()
            for position, document_index in enumerate(order, start=1):
                seen.add(int(document_index))
                rows.append(
                    RankedPair(
                        query_index=query_index,
                        document_index=int(document_index),
                        rank=position,
                        similarity=float(similarities[document_index]),
                    )
                )
            facts = queries[query_index]
            for document_index in (*facts.positives, *facts.negatives):
                if document_index in seen:
                    continue
                seen.add(document_index)
                similarity = similarities[document_index]
                # 정확한 순위: 이 문서보다 더 가까운 문서 수 + 1
                rank = int(np.count_nonzero(similarities > similarity)) + 1
                rows.append(
                    RankedPair(
                        query_index=query_index,
                        document_index=document_index,
                        rank=rank,
                        similarity=float(similarity),
                    )
                )
            if facts.positives:
                best_positive[query_index] = float(similarities[list(facts.positives)].max())
    return rows, best_positive


def _kpi(
    rankings: Sequence[RankedPair], queries: Sequence[QueryFacts]
) -> dict[str, float | int | str] | None:
    """학습에 쓰는 질의 전부(정답이 있는 것)의 Recall@10 · MRR@10."""
    eval_indices = [
        index for index, query in enumerate(queries) if query.included and query.positives
    ]
    if not eval_indices:
        return None
    wanted = set(eval_indices)
    ranks: dict[int, dict[int, int]] = {}
    for row in rankings:
        if row.query_index in wanted:
            ranks.setdefault(row.query_index, {})[row.document_index] = row.rank
    recall_sum = 0.0
    reciprocal_sum = 0.0
    for index in eval_indices:
        positives = queries[index].positives
        found = ranks.get(index, {})
        positive_ranks = [found[document] for document in positives if document in found]
        within = [rank for rank in positive_ranks if rank <= KPI_AT]
        recall_sum += len(within) / len(positives)
        if within:
            reciprocal_sum += 1 / min(within)
    count = len(eval_indices)
    return {
        "recall_at_10": round(recall_sum / count, 4),
        "mrr_at_10": round(reciprocal_sum / count, 4),
        "evaluated": count,
    }


def _candidates(
    rankings: Sequence[RankedPair],
    queries: Sequence[QueryFacts],
    best_positive: dict[int, float],
    *,
    margin: float,
) -> list[Candidate]:
    """제안 후보: 거짓 오답 → 정답 의심 → 빠진 정답 순서(Jev에 물을 차례)."""
    false_negatives: list[Candidate] = []
    suspects: list[Candidate] = []
    missing: list[Candidate] = []
    missing_count: dict[int, int] = {}
    for row in rankings:
        facts = queries[row.query_index]
        best = best_positive.get(row.query_index)
        is_negative = row.document_index in facts.negatives
        is_positive = row.document_index in facts.positives
        if is_negative and best is not None and row.similarity >= best * margin:
            false_negatives.append(_candidate("false_negative", row))
        elif is_positive and row.rank > SUSPECT_RANK_FROM:
            suspects.append(_candidate("suspect_positive", row))
        elif not is_negative and not is_positive and row.rank <= RANKING_TOP_K:
            limit = MISSING_PER_QUERY if best is not None else MISSING_TOP_FOR_UNANSWERED
            is_close = best is None or row.similarity >= best * margin
            if is_close and missing_count.get(row.query_index, 0) < limit:
                missing_count[row.query_index] = missing_count.get(row.query_index, 0) + 1
                missing.append(_candidate("missing_positive", row))
    # 가까운 것(거짓 오답 · 빠진 정답) · 먼 것(정답 의심)부터 묻는다.
    false_negatives.sort(key=lambda item: -item.similarity)
    suspects.sort(key=lambda item: -item.rank)
    missing.sort(key=lambda item: (item.rank, -item.similarity))
    return [*false_negatives, *suspects, *missing]


def _candidate(kind: str, row: RankedPair) -> Candidate:
    return Candidate(
        kind=kind,
        query_index=row.query_index,
        document_index=row.document_index,
        rank=row.rank,
        similarity=row.similarity,
    )
