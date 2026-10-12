"""생성 뜻 분석의 계산: 과제 무리 · 근접 중복(지시끼리) · 지시 불일치 후보(지시와 응답의 뜻 거리).

모두 numpy · scikit-learn 계산이고 DB를 모른다. 작업(semantic.py)이 벡터를 모아 넘기고,
계산이 길어 따로 된 스레드에서 부른다.

- 과제 무리: 지시 벡터를 KMeans로 TASK_CLUSTERS 무리로 나눈다. 가장 큰 무리의 몫이 크면 과제가 쏠린 것이다
  (코딩만 · 인사만처럼 좁게 배운다).
- 근접 중복: 지시 벡터의 이웃 NEAR_NEIGHBORS개 가운데 코사인 ≥ NEAR_DUPLICATE_SIMILARITY인 쌍.
  맥락이 글자까지 같은 쌍(같은 지시)은 뺀다. 같은 지시 검사가 이미 센다.
- 지시 불일치 후보: 지시와 응답의 코사인이 가장 낮은 MISMATCH_CANDIDATE_SHARE(적어도 MISMATCH_MIN_CANDIDATES)를
  낮은 것부터 고른다. 뜻이 멀다는 것만으로는 틀렸다고 할 수 없어, 후보는 Jev가 확인한 것만 문제로 센다.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from dent.system import neighbors

# 과제 무리 수와 씨앗 값 (같은 데이터면 같은 무리)
TASK_CLUSTERS = 8
TASK_RANDOM_STATE = 42
TASK_N_INIT = 4

# 근접 중복: 이웃 수와 유사도 경계 (분류의 근접 중복과 같은 0.92)
NEAR_NEIGHBORS = 10
NEAR_DUPLICATE_SIMILARITY = 0.92

# 지시 불일치 후보: 지시 ↔ 응답 유사도가 가장 낮은 몫과 최소 수
MISMATCH_CANDIDATE_SHARE = 0.10
MISMATCH_MIN_CANDIDATES = 20


@dataclass(frozen=True)
class NearPair:
    """근접 중복 한 쌍 (행 순서의 자리, 작은 것이 앞)."""

    index_a: int
    index_b: int
    similarity: float


@dataclass
class Findings:
    """뜻 분석 계산의 결과."""

    # 지시마다 과제 무리 번호 (행 순서)
    clusters: list[int] = field(default_factory=list)

    # 무리 수 · 무리마다 크기 · 가장 큰 무리의 몫
    cluster_count: int = 0
    cluster_sizes: list[int] = field(default_factory=list)
    largest_share: float = 0.0

    # 근접 중복 쌍
    near_pairs: list[NearPair] = field(default_factory=list)

    # 지시 ↔ 응답 코사인 유사도 (행 순서)
    similarities: list[float] = field(default_factory=list)

    # 지시 불일치 후보 (행 자리, 유사도가 낮은 것부터)
    mismatch_candidates: list[int] = field(default_factory=list)


def analyze(
    instruction_vectors: np.ndarray,
    response_vectors: np.ndarray,
    context_hashes: Sequence[str],
) -> Findings:
    """지시 · 응답 벡터(행 순서)와 맥락 해시로 뜻 분석을 계산한다."""
    instructions = _normalized(instruction_vectors)
    responses = _normalized(response_vectors)
    findings = Findings()
    _cluster(findings, instructions)
    findings.near_pairs = _near_pairs(instructions, context_hashes)
    if len(instructions):
        similarities = np.einsum("ij,ij->i", instructions, responses)
        findings.similarities = [float(value) for value in similarities]
        wanted = max(MISMATCH_MIN_CANDIDATES, round(len(similarities) * MISMATCH_CANDIDATE_SHARE))
        order = np.argsort(similarities, kind="stable")[:wanted]
        findings.mismatch_candidates = [int(index) for index in order]
    return findings


def _normalized(vectors: np.ndarray) -> np.ndarray:
    """길이를 1로 맞춘 float32 벡터 (코사인 유사도 = 내적)."""
    unit = np.asarray(vectors, dtype=np.float32)
    if unit.size == 0:
        return unit
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)


def _cluster(findings: Findings, unit: np.ndarray) -> None:
    """지시를 KMeans로 무리 짓고 무리마다 크기 · 가장 큰 무리의 몫을 적는다."""
    count = len(unit)
    if count == 0:
        return
    # 같은 벡터(같은 지시)가 많으면 서로 다른 벡터 수보다 많이 나눌 수 없다.
    distinct = len(np.unique(unit, axis=0))
    clusters = min(TASK_CLUSTERS, distinct)
    # 쓸 때 불러온다(scikit-learn은 무겁다).
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=clusters, random_state=TASK_RANDOM_STATE, n_init=TASK_N_INIT)
    labels = model.fit_predict(unit)
    sizes = np.bincount(labels, minlength=clusters)
    findings.clusters = [int(label) for label in labels]
    findings.cluster_count = clusters
    findings.cluster_sizes = [int(size) for size in sizes]
    findings.largest_share = float(sizes.max() / count)


def _near_pairs(unit: np.ndarray, context_hashes: Sequence[str]) -> list[NearPair]:
    """이웃 가운데 유사도 경계를 넘는 쌍. 맥락이 글자까지 같은 쌍은 뺀다."""
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
            is_same_text = context_hashes[index] == context_hashes[other]
            if other == index or is_same_text:
                continue
            key = (min(index, other), max(index, other))
            pairs[key] = max(pairs.get(key, 0.0), float(similarity))
    return [NearPair(index_a=a, index_b=b, similarity=s) for (a, b), s in sorted(pairs.items())]
