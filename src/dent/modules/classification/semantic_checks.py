"""뜻 분석: 근접 중복 · 오라벨 의심 · 의미 쏠림. 의미 지도와 같은 임베딩으로 계산한다(지도 한 줄 = 뜻 분석 한 번).

계산 (analyze, 작업 실행기의 스레드에서): 서로 다른 문장마다 벡터와 사실(라벨)을 받아
  - 근접 중복: 가까운 이웃 가운데 유사도 ≥ NEAR_DUPLICATE_MIN_SIMILARITY인 쌍(글자가 다른 문장끼리)
  - 오라벨 의심: 임베딩으로 라벨을 맞히는 분류기(로지스틱 회귀)를 5겹 교차 검증으로 돌려,
    자기 자신은 빼고 학습한 모델이 매긴 지금 라벨 확률이 낮고 다른 라벨이 높은 문장
  - 의미 쏠림: 라벨마다 문장을 무리로 나눠(KMeans) 가장 큰 무리의 비율 · 고르기와 대표 문장
Jev 판정 (semantic_map이 묻는다): 오라벨 의심을 Jev에 한 번 더 묻고, Jev도 다른 라벨을 고르고 지금 라벨 확률이
  기준 아래면 '확인'으로 적는다. 결과는 제안이고 사람이 받아들여야 반영한다.
저장과 읽기: 쌍 · 의심을 표에 적고 요약을 지도의 checks에 둔다. 제안 탭이 의심을 수락(라벨 바꾸기) · 유지한다.
유지한 의심은 다음 뜻 분석으로 이어 간다(같은 문장 · 같은 라벨이면 다시 묻지 않는다).

기준값은 아래 상수이고, 분석마다 checks.thresholds에 남긴다(임베딩 모델마다 알맞은 값이 다를 수 있어서).
2026-09-25 bge-m3 · 한국어 뉴스 제목 971개로 정했다: 진짜 근접 중복 0.985, 그다음 쌍 0.76 /
분류기 기준에 22건이 걸리고, laya(Jev)가 그 가운데 16건을 같은 판정으로 확인했다.
"""

import math
import warnings
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.classification import records as records_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import (
    Label,
    LabelSuspect,
    Map,
    NearDuplicate,
    Record,
    SuspectDecision,
)
from dent.modules.classification.schemas import Grade
from dent.system import datasets as datasets_service
from dent.system import neighbors
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.jev import JevDecision

# 근접 중복으로 보는 최소 코사인 유사도
NEAR_DUPLICATE_MIN_SIMILARITY = 0.92

# 문장마다 찾는 가까운 이웃 수. 근접 중복 쌍을 여기서 고른다.
NEIGHBOR_COUNT = 10

# 오라벨 의심: 분류기가 매긴 지금 라벨 확률이 이보다 낮고
# 가장 높은 다른 라벨의 확률이 이 이상이면 의심으로 본다(둘 다 애매하면 빼려고).
# 라벨당 수십 문장인 작은 데이터에서는 교차 검증 확률이 덜 뾰족해서 0.1 · 0.6이면 심은 뒤집기의 절반만 잡혔다
# (검증: 문의 의도 15 → 27/30, 백과 문장 주제 20 → 23/25). 더 잡힌 오탐은 Jev가 지금 라벨을 고르면 유지한다.
SUSPECT_MAX_LABEL_PROBABILITY = 0.20
SUSPECT_MIN_SUGGESTED_PROBABILITY = 0.50

# Jev도 다른 라벨을 고르고 지금 라벨 확률이 이보다 낮으면 '확인'으로 본다.
JEV_CONFIRM_MAX_LABEL_PROBABILITY = 0.20

# Jev에 묻는 의심의 최대 수. laya는 1건에 30~60ms라서 2,000건이면 2분쯤이다(가장 의심스러운 것부터 묻는다).
MAX_JEV_JUDGMENTS = 2_000

# 의미 쏠림: 가장 큰 무리가 라벨 문장의 이 비율 이상이면 쏠림으로 본다(무리 8개면 고르게는 12.5%).
SKEW_MIN_LARGEST_SHARE = 0.40

# 라벨 안을 나누는 무리 수
SKEW_CLUSTER_COUNT = 8

# 의미 쏠림을 재는 최소 문장 수. 무리마다 5개쯤은 있어야 비율이 뜻을 가진다.
SKEW_MIN_TEXTS = SKEW_CLUSTER_COUNT * 5

# 가장 큰 무리에서 보여 줄 대표 문장 수와 그 글자 수
SKEW_EXAMPLE_COUNT = 2
SKEW_EXAMPLE_LENGTH = 80

# 오라벨 분류기: 교차 검증 겹 수(라벨마다 이만큼은 있어야 한다), 규제 세기, 반복 수
CROSS_VALIDATION_FOLDS = 5
CLASSIFIER_C = 4.0
CLASSIFIER_MAX_ITER = 1_000

# 같은 데이터면 같은 결과가 나오게 고정하는 씨앗 값 (교차 검증 나누기 · KMeans)
RANDOM_STATE = 42

# 오라벨 의심 등급: 대기 수 ÷ 분석한 문장 수가 1% 미만이면 통과, 3% 미만이면 주의, 그 위는 심각.
# 분류기 의심에는 헛짚음이 섞이므로 0건을 통과 기준으로 두지 않는다.
SUSPECT_GOOD_BELOW_RATE = 0.01
SUSPECT_WARN_BELOW_RATE = 0.03

# 쌍 · 의심을 표에 넣을 때 한 번에 보내는 줄 수
SAVE_CHUNK = 5_000

# 오라벨 의심 목록 한 쪽의 최대 줄 수, 한 문장의 근접 중복 목록 최대 수
MAX_SUSPECT_PAGE_SIZE = 200
NEAR_DUPLICATE_LIST_LIMIT = 20

# Jev에 묻는 질문 이름과 지시
JEV_QUESTION = "label"
JEV_INSTRUCTIONS = "문장에 맞는 라벨"

# Jev 판정 상태 (checks.jev.status)
JEV_JUDGED = "judged"
JEV_NOT_CONNECTED = "not_connected"
JEV_FAILED = "failed"
JEV_CANCELED = "canceled"

NO_ANALYSIS_MESSAGE = "뜻 분석이 없습니다. 먼저 만들어 주세요."
SUSPECT_NOT_FOUND_MESSAGE = "오라벨 의심을 찾을 수 없습니다."
ALREADY_DECIDED_MESSAGE = "이미 판단한 제안입니다."


# ---------- 계산 ----------


@dataclass(frozen=True)
class TextFacts:
    """뜻 분석에 쓰는 서로 다른 문장(해시) 하나의 사실."""

    # 라벨. 같은 문장이 여러 라벨이면(라벨 충돌 · 따로 잡힌다) 또는 라벨이 없으면 None
    label_id: int | None


@dataclass(frozen=True)
class NearPair:
    """근접 중복 한 쌍 (문장 자리 a < b)."""

    # 앞 문장 자리
    a: int

    # 뒤 문장 자리
    b: int

    # 코사인 유사도
    similarity: float


@dataclass(frozen=True)
class SuspectFinding:
    """오라벨 의심 한 건 (분류기 근거)."""

    # 문장 자리
    index: int

    # 지금 라벨
    label_id: int

    # 추천 라벨
    suggested_label_id: int

    # 지금 라벨 확률 (교차 검증)
    label_probability: float

    # 추천 라벨 확률
    suggested_probability: float


@dataclass(frozen=True)
class SkewFinding:
    """라벨 하나의 의미 쏠림."""

    label_id: int

    # 라벨의 서로 다른 문장 수
    text_count: int

    # 나눈 무리 수
    cluster_count: int

    # 가장 큰 무리의 비율 (0~1)
    largest_share: float

    # 무리 크기의 고르기 (정규화한 엔트로피, 1이면 고름)
    evenness: float

    # 가장 큰 무리의 대표 문장 자리
    example_indices: list[int]

    # 쏠림인지 (가장 큰 무리 ≥ SKEW_MIN_LARGEST_SHARE)
    is_skewed: bool


@dataclass(frozen=True)
class Findings:
    """뜻 분석 한 번의 계산 결과. 자리는 받은 문장 순서다."""

    pairs: list[NearPair]

    # 가장 의심스러운 것(지금 라벨 확률이 낮은 것)부터
    suspects: list[SuspectFinding]

    skews: list[SkewFinding]

    # 문장이 적어 오라벨 의심을 재지 못한 라벨 (교차 검증 겹 수보다 적음)
    unjudged_label_ids: list[int] = field(default_factory=list)


def analyze(vectors: np.ndarray, facts: Sequence[TextFacts]) -> Findings:
    """문장마다 벡터와 사실을 받아 근접 중복 · 오라벨 의심 · 의미 쏠림을 계산한다. 오래 걸려 스레드에서 부른다."""
    unit = normalized(vectors)
    labels = [fact.label_id for fact in facts]
    found = neighbors.nearest_neighbors(unit, k=NEIGHBOR_COUNT)
    suspects, unjudged = _find_suspects(unit, labels)
    return Findings(
        pairs=_find_pairs(found),
        suspects=suspects,
        skews=_measure_skews(unit, labels),
        unjudged_label_ids=unjudged,
    )


def _find_pairs(found: neighbors.Neighbors) -> list[NearPair]:
    """이웃 가운데 기준 이상인 쌍. 서로 이웃이면 두 번 나오므로 한 번만 남긴다. 유사도가 큰 것부터."""
    pairs: dict[tuple[int, int], float] = {}
    rows, columns = np.nonzero(found.similarities >= NEAR_DUPLICATE_MIN_SIMILARITY)
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        a, b = sorted((row, int(found.indices[row, column])))
        pairs[(a, b)] = float(found.similarities[row, column])
    return [
        NearPair(a=a, b=b, similarity=similarity)
        for (a, b), similarity in sorted(pairs.items(), key=lambda item: -item[1])
    ]


def _find_suspects(
    unit: np.ndarray, labels: Sequence[int | None]
) -> tuple[list[SuspectFinding], list[int]]:
    """교차 검증 분류기로 오라벨 의심을 찾는다. 문장이 겹 수보다 적은 라벨은 재지 못한다(두 번째 값)."""
    # scikit-learn은 불러오는 데 시간이 걸려서 쓸 때 불러온다(API 서버가 켜질 때 느려지지 않게).
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    counts = Counter(label for label in labels if label is not None)
    unjudged = sorted(label for label, count in counts.items() if count < CROSS_VALIDATION_FOLDS)
    used = [
        index
        for index, label in enumerate(labels)
        if label is not None and counts[label] >= CROSS_VALIDATION_FOLDS
    ]
    classes = sorted({labels[index] for index in used})
    if len(classes) < 2:
        return [], sorted(counts)
    y = np.array([labels[index] for index in used])
    folds = StratifiedKFold(
        n_splits=CROSS_VALIDATION_FOLDS, shuffle=True, random_state=RANDOM_STATE
    )
    classifier = LogisticRegression(C=CLASSIFIER_C, max_iter=CLASSIFIER_MAX_ITER)
    with warnings.catch_warnings():
        # 다 못 모여도 확률은 쓸 만하다. 로그만 어지럽히지 않게 끈다.
        warnings.simplefilter("ignore", ConvergenceWarning)
        probabilities = cross_val_predict(
            classifier, unit[used], y, cv=folds, method="predict_proba"
        )
    # 확률의 열은 정렬한 라벨 순서(classes)다.
    column_of = {label: column for column, label in enumerate(classes)}
    suspects = []
    for row, index in enumerate(used):
        given = float(probabilities[row, column_of[y[row]]])
        best_column = int(probabilities[row].argmax())
        best = float(probabilities[row, best_column])
        is_suspect = (
            classes[best_column] != y[row]
            and given < SUSPECT_MAX_LABEL_PROBABILITY
            and best >= SUSPECT_MIN_SUGGESTED_PROBABILITY
        )
        if is_suspect:
            suspects.append(
                SuspectFinding(
                    index=index,
                    label_id=int(y[row]),
                    suggested_label_id=int(classes[best_column]),
                    label_probability=given,
                    suggested_probability=best,
                )
            )
    suspects.sort(key=lambda suspect: (suspect.label_probability, suspect.index))
    return suspects, unjudged


def _measure_skews(unit: np.ndarray, labels: Sequence[int | None]) -> list[SkewFinding]:
    """라벨마다 문장을 무리로 나눠 가장 큰 무리의 비율과 고르기를 잰다. 문장이 적은 라벨은 건너뛴다."""
    from sklearn.cluster import KMeans

    skews = []
    for label in sorted({label for label in labels if label is not None}):
        members = np.array([index for index, value in enumerate(labels) if value == label])
        if len(members) < SKEW_MIN_TEXTS:
            continue
        clusters = KMeans(n_clusters=SKEW_CLUSTER_COUNT, n_init=3, random_state=RANDOM_STATE).fit(
            unit[members]
        )
        sizes = np.bincount(clusters.labels_, minlength=SKEW_CLUSTER_COUNT)
        shares = sizes[sizes > 0] / len(members)
        evenness = float(-(shares * np.log(shares)).sum() / math.log(SKEW_CLUSTER_COUNT))
        largest = int(sizes.argmax())
        in_largest = members[clusters.labels_ == largest]
        # 무리 가운데에 가장 가까운 문장이 그 무리를 가장 잘 보여 준다.
        closeness = unit[in_largest] @ clusters.cluster_centers_[largest]
        examples = in_largest[np.argsort(-closeness)[:SKEW_EXAMPLE_COUNT]]
        largest_share = float(sizes[largest] / len(members))
        skews.append(
            SkewFinding(
                label_id=int(label),
                text_count=len(members),
                cluster_count=SKEW_CLUSTER_COUNT,
                largest_share=largest_share,
                evenness=evenness,
                example_indices=[int(index) for index in examples],
                is_skewed=largest_share >= SKEW_MIN_LARGEST_SHARE,
            )
        )
    return skews


def normalized(vectors: np.ndarray) -> np.ndarray:
    """길이를 1로 맞춘 float32 벡터 (코사인 유사도 = 내적)."""
    unit = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)


# ---------- Jev 판정 ----------


@dataclass(frozen=True)
class JevVerdict:
    """오라벨 의심 하나에 대한 Jev 판정."""

    # Jev가 고른 라벨. 고른 이름이 라벨 목록에 없으면 None
    label_id: int | None

    # Jev가 매긴 지금 라벨의 확률
    label_probability: float | None

    # Jev의 확신도
    confidence: float | None

    # Jev도 지금 라벨이 아니라고 봤는지
    is_confirmed: bool


def jev_questions(labels: Sequence[Label]) -> dict[str, Any]:
    """오라벨 의심을 묻는 Jev 질문: 라벨 이름을 선택지로, 라벨 설명(없으면 이름)을 기준으로 준다."""
    criteria = {label.name: label.description or label.name for label in labels}
    return {
        JEV_QUESTION: {"type": "choice", "instructions": JEV_INSTRUCTIONS, "criteria": criteria}
    }


def read_verdict(decision: JevDecision, *, labels: Sequence[Label], label_id: int) -> JevVerdict:
    """Jev 판정을 라벨 번호로 바꾸고, 지금 라벨(label_id)이 아니라고 확인했는지 본다."""
    answer = decision.answers.get(JEV_QUESTION)
    id_by_name = {label.name: label.id for label in labels}
    name_by_id = {label.id: label.name for label in labels}
    if answer is None:
        return JevVerdict(
            label_id=None, label_probability=None, confidence=None, is_confirmed=False
        )
    chosen = id_by_name.get(answer.choice or "")
    given_probability = answer.probabilities.get(name_by_id[label_id])
    is_confirmed = (
        chosen is not None
        and chosen != label_id
        and given_probability is not None
        and given_probability < JEV_CONFIRM_MAX_LABEL_PROBABILITY
    )
    return JevVerdict(
        label_id=chosen,
        label_probability=given_probability,
        confidence=answer.confidence,
        is_confirmed=is_confirmed,
    )


# ---------- 저장 ----------


async def save_findings(
    db: AsyncSession,
    *,
    map_row: Map,
    text_hashes: Sequence[str],
    facts: Sequence[TextFacts],
    findings: Findings,
    verdicts: dict[int, JevVerdict],
    jev_state: dict[str, Any],
    example_texts: dict[str, str],
) -> None:
    """쌍 · 의심을 표에 적고 요약을 지도의 checks에 둔다. 커밋은 부른 쪽이 한다.

    같은 문장 · 같은 라벨을 앞서 '유지'했으면 이번에도 유지로 적는다(다시 묻지 않게).
    """
    map_id = map_row.id
    pair_rows = [
        {
            "map_id": map_id,
            "text_hash_a": min(text_hashes[pair.a], text_hashes[pair.b]),
            "text_hash_b": max(text_hashes[pair.a], text_hashes[pair.b]),
            "similarity": pair.similarity,
        }
        for pair in findings.pairs
    ]
    for start in range(0, len(pair_rows), SAVE_CHUNK):
        await db.execute(insert(NearDuplicate), pair_rows[start : start + SAVE_CHUNK])

    kept = await _kept_suspects(db, dataset_id=map_row.dataset_id, except_map_id=map_id)
    suspect_rows = []
    for suspect in findings.suspects:
        text_hash = text_hashes[suspect.index]
        verdict = verdicts.get(suspect.index)
        kept_at = kept.get((text_hash, suspect.label_id))
        suspect_rows.append(
            {
                "map_id": map_id,
                "text_hash": text_hash,
                "label_id": suspect.label_id,
                "suggested_label_id": suspect.suggested_label_id,
                "label_probability": suspect.label_probability,
                "suggested_probability": suspect.suggested_probability,
                "jev_label_id": verdict.label_id if verdict else None,
                "jev_label_probability": verdict.label_probability if verdict else None,
                "jev_confidence": verdict.confidence if verdict else None,
                "is_confirmed": verdict.is_confirmed if verdict else False,
                "decision": SuspectDecision.KEPT if kept_at else None,
                "decided_at": kept_at,
            }
        )
    for start in range(0, len(suspect_rows), SAVE_CHUNK):
        await db.execute(insert(LabelSuspect), suspect_rows[start : start + SAVE_CHUNK])

    map_row.checks = _summary(
        text_hashes=text_hashes,
        facts=facts,
        findings=findings,
        verdicts=verdicts,
        jev_state=jev_state,
        example_texts=example_texts,
    )


def _summary(
    *,
    text_hashes: Sequence[str],
    facts: Sequence[TextFacts],
    findings: Findings,
    verdicts: dict[int, JevVerdict],
    jev_state: dict[str, Any],
    example_texts: dict[str, str],
) -> dict[str, Any]:
    """지도의 checks에 둘 요약. 쌍 · 의심의 수, 라벨마다 쏠림, 기준값, Jev 상태."""
    label_mismatch = sum(
        1
        for pair in findings.pairs
        if None not in (facts[pair.a].label_id, facts[pair.b].label_id)
        and facts[pair.a].label_id != facts[pair.b].label_id
    )
    pair_texts = {index for pair in findings.pairs for index in (pair.a, pair.b)}
    return {
        "thresholds": {
            "near_duplicate_similarity": NEAR_DUPLICATE_MIN_SIMILARITY,
            "suspect_label_probability": SUSPECT_MAX_LABEL_PROBABILITY,
            "suspect_suggested_probability": SUSPECT_MIN_SUGGESTED_PROBABILITY,
            "jev_confirm_label_probability": JEV_CONFIRM_MAX_LABEL_PROBABILITY,
            "skew_largest_share": SKEW_MIN_LARGEST_SHARE,
        },
        "near_duplicates": {
            "pairs": len(findings.pairs),
            "texts": len(pair_texts),
            "label_mismatch": label_mismatch,
        },
        "suspects": {
            "found": len(findings.suspects),
            "judged": len(verdicts),
            "confirmed": sum(1 for verdict in verdicts.values() if verdict.is_confirmed),
            "unjudged_label_ids": findings.unjudged_label_ids,
        },
        "jev": jev_state,
        "skews": [
            {
                "label_id": skew.label_id,
                "text_count": skew.text_count,
                "cluster_count": skew.cluster_count,
                "largest_share": round(skew.largest_share, 4),
                "evenness": round(skew.evenness, 4),
                "is_skewed": skew.is_skewed,
                "examples": [
                    example_texts.get(text_hashes[index], "")[:SKEW_EXAMPLE_LENGTH]
                    for index in skew.example_indices
                ],
            }
            for skew in findings.skews
        ],
    }


async def _kept_suspects(
    db: AsyncSession, *, dataset_id: int, except_map_id: int
) -> dict[tuple[str, int], datetime]:
    """데이터셋의 앞선 뜻 분석에서 '유지'한 의심: (문장 해시, 라벨) → 판단 시각."""
    rows = await db.execute(
        select(LabelSuspect.text_hash, LabelSuspect.label_id, LabelSuspect.decided_at)
        .join(Map, Map.id == LabelSuspect.map_id)
        .where(
            Map.dataset_id == dataset_id,
            Map.id != except_map_id,
            LabelSuspect.decision == SuspectDecision.KEPT,
        )
    )
    return {(text_hash, label_id): decided_at for text_hash, label_id, decided_at in rows}


# ---------- 읽기 ----------


@dataclass(frozen=True)
class SkewLabel:
    """화면에 보일 라벨 하나의 의미 쏠림 (라벨 이름은 지금 이름)."""

    label_id: int
    label_name: str
    text_count: int
    cluster_count: int
    largest_share: float
    evenness: float
    is_skewed: bool
    examples: list[str]


@dataclass(frozen=True)
class SuspectCounts:
    """오라벨 의심 수. 판단은 계산 뒤에 바뀌므로 표에서 바로 센다."""

    # 이번 분석에서 찾은 수
    found: int

    # 아직 판단하지 않은 수
    open: int

    # 그 가운데 Jev도 확인한 수
    confirmed_open: int

    # 수락 · 유지한 수
    accepted: int
    kept: int

    # Jev에 물은 수
    judged: int

    # 문장이 적어 재지 못한 라벨 수
    unjudged_labels: int

    # 대기 수 ÷ 분석한 문장 수 (등급을 매기는 값)
    open_rate: float

    # 라벨 번호 → 그 라벨의 대기 수 (대기가 없는 라벨은 빠진다)
    open_by_label: dict[int, int]


@dataclass(frozen=True)
class Checks:
    """뜻 분석 한 번의 요약 (화면의 진단 · 제안 탭이 쓴다)."""

    thresholds: dict[str, float]

    # 근접 중복: pairs · texts · label_mismatch
    near_duplicates: dict[str, int]

    suspects: SuspectCounts

    # Jev 판정 상태: status(judged · not_connected · failed · canceled)와 detail
    jev: dict[str, Any]

    # 쏠림을 잰 라벨들 (이름 순)
    skews: list[SkewLabel]

    # 문장이 적어 쏠림을 재지 못한 라벨 수
    unmeasured_labels: int

    # 검사마다 등급: suspect · near_duplicate · skew
    grades: dict[str, Grade]


async def read_checks(db: AsyncSession, *, map_row: Map) -> Checks | None:
    """다 만든 뜻 분석의 요약. 뜻 분석이 생기기 전에 만든 지도면(요약이 비었으면) None."""
    summary = map_row.checks
    if not summary:
        return None
    labels = {
        label.id: label.name
        for label in await db.scalars(select(Label).where(Label.dataset_id == map_row.dataset_id))
    }
    by_decision = dict(
        (
            await db.execute(
                select(LabelSuspect.decision, func.count())
                .where(LabelSuspect.map_id == map_row.id)
                .group_by(LabelSuspect.decision)
            )
        )
        .tuples()
        .all()
    )
    open_by_label = dict(
        (
            await db.execute(
                select(LabelSuspect.label_id, func.count())
                .where(LabelSuspect.map_id == map_row.id, LabelSuspect.decision.is_(None))
                .group_by(LabelSuspect.label_id)
            )
        )
        .tuples()
        .all()
    )
    confirmed_open = await db.scalar(
        select(func.count())
        .select_from(LabelSuspect)
        .where(
            LabelSuspect.map_id == map_row.id,
            LabelSuspect.decision.is_(None),
            LabelSuspect.is_confirmed,
        )
    )
    suspects = summary["suspects"]
    skews = sorted(
        (
            SkewLabel(
                label_id=skew["label_id"],
                label_name=labels.get(skew["label_id"], "—"),
                text_count=skew["text_count"],
                cluster_count=skew["cluster_count"],
                largest_share=skew["largest_share"],
                evenness=skew["evenness"],
                is_skewed=skew["is_skewed"],
                examples=skew["examples"],
            )
            for skew in summary["skews"]
        ),
        key=lambda skew: skew.label_name,
    )
    open_count = by_decision.get(None, 0)
    open_rate = open_count / map_row.text_count if map_row.text_count else 0.0
    return Checks(
        # 등급 경계는 지금 상수로 알린다(앞서 만든 분석에도 같은 기준을 적용한다).
        thresholds={
            **summary["thresholds"],
            "suspect_good_rate": SUSPECT_GOOD_BELOW_RATE,
            "suspect_warn_rate": SUSPECT_WARN_BELOW_RATE,
        },
        near_duplicates=summary["near_duplicates"],
        suspects=SuspectCounts(
            found=suspects["found"],
            open=open_count,
            confirmed_open=int(confirmed_open or 0),
            accepted=by_decision.get(SuspectDecision.ACCEPTED, 0),
            kept=by_decision.get(SuspectDecision.KEPT, 0),
            judged=suspects["judged"],
            unjudged_labels=len(suspects["unjudged_label_ids"]),
            open_rate=open_rate,
            open_by_label=open_by_label,
        ),
        jev=summary["jev"],
        skews=skews,
        unmeasured_labels=max(len(labels) - len(skews), 0),
        grades=_grades(
            open_rate=open_rate, near_duplicates=summary["near_duplicates"], skews=skews
        ),
    )


def _grades(
    *, open_rate: float, near_duplicates: dict[str, int], skews: Sequence[SkewLabel]
) -> dict[str, Grade]:
    """뜻 검사 세 가지의 등급 (글자 검사와 같은 good · warn · bad).

    근접 중복은 라벨이 다른 쌍이 있으면 주의.
    의미 쏠림은 쏠린 라벨이 하나라도 있으면 주의(데이터를 더 모아야 해서 심각으로 두지 않는다).
    """
    if open_rate < SUSPECT_GOOD_BELOW_RATE:
        suspect: Grade = "good"
    elif open_rate < SUSPECT_WARN_BELOW_RATE:
        suspect = "warn"
    else:
        suspect = "bad"
    near_duplicate: Grade = "warn" if near_duplicates["label_mismatch"] else "good"
    has_skew = any(skew.is_skewed for skew in skews)
    skew_grade: Grade = "warn" if has_skew else "good"
    return {"suspect": suspect, "near_duplicate": near_duplicate, "skew": skew_grade}


@dataclass(frozen=True)
class SuspectItem:
    """화면에 보일 오라벨 의심 한 건."""

    suspect: LabelSuspect

    # 문장 (같은 문장이면 가장 먼저 들어온 것)
    text: str

    # 이 문장 · 라벨인 휴지통 밖 문장 수 (수락하면 이만큼 바뀐다)
    record_count: int

    label_name: str
    suggested_label_name: str

    # Jev가 고른 라벨 이름. 묻지 않았으면 None
    jev_label_name: str | None


async def list_suspects(
    db: AsyncSession,
    *,
    dataset_id: int,
    decision: str,
    confirmed_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[SuspectItem], int]:
    """다 만든 뜻 분석의 오라벨 의심 한 쪽과 전체 수. Jev가 확인한 것 · 지금 라벨 확률이 낮은 것부터.

    decision: open(아직) · accepted · kept · all. 데이터셋이 없으면 NotFoundError, 뜻 분석이 없으면 빈 목록.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    conditions = [LabelSuspect.map_id == records_service.done_map_id(dataset_id)]
    if decision == "open":
        conditions.append(LabelSuspect.decision.is_(None))
    elif decision != "all":
        conditions.append(LabelSuspect.decision == decision)
    if confirmed_only:
        conditions.append(LabelSuspect.is_confirmed)

    total = await db.scalar(select(func.count()).select_from(LabelSuspect).where(*conditions))
    current, suggested, judged = aliased(Label), aliased(Label), aliased(Label)
    same_text = (Record.dataset_id == dataset_id, Record.text_hash == LabelSuspect.text_hash)
    first_text = (
        select(Record.text).where(*same_text).order_by(Record.id).limit(1).scalar_subquery()
    )
    record_count = (
        select(func.count())
        .where(
            *same_text,
            Record.label_id == LabelSuspect.label_id,
            classification_service.IS_ACTIVE,
        )
        .scalar_subquery()
    )
    query = (
        select(LabelSuspect, first_text, record_count, current.name, suggested.name, judged.name)
        .join(current, current.id == LabelSuspect.label_id)
        .join(suggested, suggested.id == LabelSuspect.suggested_label_id)
        .outerjoin(judged, judged.id == LabelSuspect.jev_label_id)
        .where(*conditions)
        .order_by(
            LabelSuspect.is_confirmed.desc(),
            LabelSuspect.label_probability,
            LabelSuspect.text_hash,
        )
        .limit(min(limit, MAX_SUSPECT_PAGE_SIZE))
        .offset(offset)
    )
    items = [
        SuspectItem(
            suspect=suspect,
            text=text or "",
            record_count=int(count or 0),
            label_name=label_name,
            suggested_label_name=suggested_name,
            jev_label_name=jev_name,
        )
        for suspect, text, count, label_name, suggested_name, jev_name in await db.execute(query)
    ]
    return items, int(total or 0)


@dataclass(frozen=True)
class NearDuplicateItem:
    """한 문장과 근접 중복인 다른 문장 (같은 문장이면 가장 먼저 들어온 것)."""

    record_id: int
    text: str
    label_name: str | None

    # 두 문장의 코사인 유사도
    similarity: float


async def list_near_duplicates(db: AsyncSession, *, record_id: int) -> list[NearDuplicateItem]:
    """문장과 근접 중복인 다른 문장들(유사도가 큰 것부터). 문장이 없으면 NotFoundError, 뜻 분석이 없으면 빈 목록."""
    record = await records_service.get_record(db, record_id=record_id)
    map_id = records_service.done_map_id(record.dataset_id)
    front = select(NearDuplicate.text_hash_b.label("partner"), NearDuplicate.similarity).where(
        NearDuplicate.map_id == map_id, NearDuplicate.text_hash_a == record.text_hash
    )
    back = select(NearDuplicate.text_hash_a.label("partner"), NearDuplicate.similarity).where(
        NearDuplicate.map_id == map_id, NearDuplicate.text_hash_b == record.text_hash
    )
    partners = front.union_all(back).subquery()
    # 짝 문장마다 휴지통 밖에서 가장 먼저 들어온 한 건을 보인다.
    first = (
        select(Record.id, Record.text, Record.label_id, Record.text_hash)
        .where(Record.dataset_id == record.dataset_id, classification_service.IS_ACTIVE)
        .distinct(Record.text_hash)
        .order_by(Record.text_hash, Record.id)
        .subquery()
    )
    query: Select[Any] = (
        select(first.c.id, first.c.text, Label.name, partners.c.similarity)
        .join(partners, partners.c.partner == first.c.text_hash)
        .outerjoin(Label, Label.id == first.c.label_id)
        .order_by(partners.c.similarity.desc(), first.c.id)
        .limit(NEAR_DUPLICATE_LIST_LIMIT)
    )
    return [
        NearDuplicateItem(record_id=row_id, text=text, label_name=label_name, similarity=similarity)
        for row_id, text, label_name, similarity in await db.execute(query)
    ]


# ---------- 판단 (제안 탭) ----------


async def accept_suspect(
    db: AsyncSession, *, dataset_id: int, text_hash: str, label_id: int | None
) -> int:
    """오라벨 의심을 수락한다: 그 문장의 라벨을 추천(또는 고른) 라벨로 바꾸고 바뀐 문장 수를 돌려준다.

    데이터셋 · 뜻 분석 · 의심이 없으면 NotFoundError, 이미 판단했으면 ConflictError,
    다른 데이터셋의 라벨이면 InvalidInputError.
    """
    suspect = await _open_suspect(db, dataset_id=dataset_id, text_hash=text_hash)
    target = label_id if label_id is not None else suspect.suggested_label_id
    await records_service.check_label_belongs(db, label_id=target, dataset_id=dataset_id)
    changed = await _relabel(db, dataset_id=dataset_id, suspect=suspect, label_id=target)
    suspect.decision = SuspectDecision.ACCEPTED
    suspect.decided_at = datetime.now(UTC)
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return changed


async def keep_suspect(db: AsyncSession, *, dataset_id: int, text_hash: str) -> None:
    """오라벨 의심을 유지한다(지금 라벨이 맞다). 다음 뜻 분석에서도 다시 묻지 않는다.

    데이터셋 · 뜻 분석 · 의심이 없으면 NotFoundError, 이미 판단했으면 ConflictError.
    """
    suspect = await _open_suspect(db, dataset_id=dataset_id, text_hash=text_hash)
    suspect.decision = SuspectDecision.KEPT
    suspect.decided_at = datetime.now(UTC)
    await db.commit()


async def accept_confirmed_suspects(db: AsyncSession, *, dataset_id: int) -> int:
    """Jev도 확인한 오라벨 의심을 모두 수락하고(추천 라벨로) 수락한 수를 돌려준다. 데이터셋이 없으면 NotFoundError."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    suspects = list(
        await db.scalars(
            select(LabelSuspect).where(
                LabelSuspect.map_id == records_service.done_map_id(dataset_id),
                LabelSuspect.decision.is_(None),
                LabelSuspect.is_confirmed,
            )
        )
    )
    now = datetime.now(UTC)
    for suspect in suspects:
        await _relabel(
            db, dataset_id=dataset_id, suspect=suspect, label_id=suspect.suggested_label_id
        )
        suspect.decision = SuspectDecision.ACCEPTED
        suspect.decided_at = now
    if suspects:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return len(suspects)


async def _open_suspect(db: AsyncSession, *, dataset_id: int, text_hash: str) -> LabelSuspect:
    """다 만든 뜻 분석에서 아직 판단하지 않은 의심 한 건. 없으면 NotFoundError, 판단했으면 ConflictError."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    map_id = await db.scalar(select(records_service.done_map_id(dataset_id)))
    if map_id is None:
        raise NotFoundError(NO_ANALYSIS_MESSAGE)
    suspect = await db.get(LabelSuspect, (map_id, text_hash), populate_existing=True)
    if suspect is None:
        raise NotFoundError(SUSPECT_NOT_FOUND_MESSAGE)
    if suspect.decision is not None:
        raise ConflictError(ALREADY_DECIDED_MESSAGE)
    return suspect


async def _relabel(
    db: AsyncSession, *, dataset_id: int, suspect: LabelSuspect, label_id: int
) -> int:
    """의심 문장(같은 문장 · 그때 라벨 · 휴지통 밖)의 라벨을 바꾸고 바뀐 수를 돌려준다."""
    changed = await db.scalars(
        update(Record)
        .where(
            Record.dataset_id == dataset_id,
            Record.text_hash == suspect.text_hash,
            Record.label_id == suspect.label_id,
            classification_service.IS_ACTIVE,
        )
        .values(label_id=label_id, row_version=Record.row_version + 1, updated_at=func.now())
        .returning(Record.id)
        .execution_options(synchronize_session=False)
    )
    return len(changed.all())
