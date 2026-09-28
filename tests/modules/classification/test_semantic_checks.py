"""뜻 분석 테스트: 근접 중복 · 오라벨 의심 · 의미 쏠림 계산, 뜻 분석 작업, 제안(수락 · 유지), 문제 거르기.

임베딩 서버는 문장마다 정해 둔 벡터를 주는 가짜를 쓴다(뜻이 가까운 문장을 일부러 가깝게 놓으려고).
Jev는 라벨 이름으로 답하는 가짜를 쓴다.
"""

import json

import httpx
import numpy as np
import pytest
from fastapi import status
from sqlalchemy import select

from dent.modules.classification import semantic_checks, semantic_map
from dent.modules.classification.models import LabelSuspect, Map, Record, SuspectDecision
from dent.system import connections as connections_service
from dent.system.models import ConnectionRole
from dent.system.text import normalize_text
from tests.modules.classification.helpers import Row, build_map, connect_embedding
from tests.system.helpers import JEV_URL, refused_transport

API = "/api/v1/classification"

# 가짜 벡터의 차원. 축 0 · 1은 두 무리, 나머지는 문장마다 하나씩 주는 자기 축이다.
DIM = 48

# 라벨마다 문장 수. 적으면 분류기가 규제 때문에 확신하지 못해 오라벨 의심 기준(0.1)을 못 넘는다.
PER_LABEL = 20

# 자기 축의 무게. 같은 무리 두 문장의 유사도가 1 / (1 + 0.6²) ≈ 0.74로 근접 중복 기준보다 한참 낮다.
OWN_AXIS_WEIGHT = 0.6

# 가 라벨인데 뜻은 나 무리에 있는 문장 (오라벨 의심이 되어야 한다)
MISLABELED = "헷갈린 문장"

# 가 문장 0과 뜻이 거의 같은 문장 (근접 중복이 되어야 한다). 라벨이 같아 규칙으로 하나만 남긴다.
NEAR_TWIN = "가 문장 0 종합"


def _vector(group: int, own: int) -> list[float]:
    """무리 축 group과 자기 축 own을 가진 벡터."""
    vector = np.zeros(DIM)
    vector[group] = 1.0
    vector[2 + own] = OWN_AXIS_WEIGHT
    return vector.tolist()


def _twin_of(vector: list[float]) -> list[float]:
    """거의 같은 벡터 (유사도 0.99 넘게)."""
    twin = np.array(vector)
    twin[-1] = 0.05
    return twin.tolist()


def _rows_and_vectors() -> tuple[list[Row], dict[str, list[float]]]:
    """가 무리 · 나 무리와 오라벨 문장 · 근접 중복 쌍."""
    rows, vectors = [], {}
    for index in range(PER_LABEL):
        for group, label in enumerate(("가", "나")):
            text = f"{label} 문장 {index}"
            rows.append(Row(text, label))
            vectors[text] = _vector(group, len(vectors))
    rows.append(Row(MISLABELED, "가"))
    vectors[MISLABELED] = _vector(1, len(vectors))
    rows.append(Row(NEAR_TWIN, "가"))
    vectors[NEAR_TWIN] = _twin_of(vectors["가 문장 0"])
    return rows, vectors


def table_transport(vectors: dict[str, list[float]]) -> httpx.MockTransport:
    """가짜 임베딩 서버: 문장마다 정해 둔 벡터를 준다(연결 확인 문장은 첫 벡터)."""
    first = next(iter(vectors.values()))

    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [
            {"index": index, "embedding": vectors.get(text, first)}
            for index, text in enumerate(texts)
        ]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(reply)


def label_jev_transport(choice: str, probabilities: dict[str, float]) -> httpx.MockTransport:
    """가짜 Jev: 라벨 질문에 늘 choice를 고르고 probabilities를 준다."""
    asked: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append(body["state"]["text"])
        answer = {
            "type": "choice",
            "choice": choice,
            "probabilities": probabilities,
            "confidence": 0.9,
        }
        return httpx.Response(
            200,
            json={"model": "laya", "answers": {name: answer for name in body["questions"]}},
        )

    transport = httpx.MockTransport(reply)
    transport.asked = asked  # type: ignore[attr-defined]
    return transport


async def _connect_jev(db_session) -> None:
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )


def _embedding_transport() -> httpx.MockTransport:
    """가 · 나 무리 벡터를 주는 가짜 임베딩 서버 (서버는 정규화한 문장을 받는다)."""
    _rows, vectors = _rows_and_vectors()
    return table_transport({normalize_text(text): vector for text, vector in vectors.items()})


async def _analyzed(db_session, make_dataset, *, jev: httpx.MockTransport | None = None):
    """문장을 넣고, 연결하고, 뜻 분석을 끝까지 돌린다. (만든 데이터셋, 상태)."""
    rows, _vectors = _rows_and_vectors()
    made = await make_dataset("뜻 분석", rows)
    await connect_embedding(db_session)
    if jev is not None:
        await _connect_jev(db_session)
    state = await build_map(
        db_session,
        dataset_id=made.dataset.id,
        transport=_embedding_transport(),
        jev_transport=jev,
    )
    return made, state


def _hash_of(made, text: str) -> str:
    return next(record.text_hash for record in made.records if record.text == text)


def _confirming_jev() -> httpx.MockTransport:
    """나를 고르고 가의 확률을 아주 낮게 주는 Jev (오라벨 의심을 확인한다)."""
    return label_jev_transport("나", {"가": 0.02, "나": 0.98})


# ---------- 계산 ----------


def test_analyze_finds_near_duplicate_pair_and_suspect_label():
    # 준비
    vectors = [_vector(0, own) for own in range(PER_LABEL)]
    vectors += [_vector(1, PER_LABEL + own) for own in range(PER_LABEL)]
    vectors.append(_vector(1, 2 * PER_LABEL))  # 가 라벨인데 나 쪽
    vectors.append(_twin_of(vectors[0]))  # 0과 거의 같음
    facts = [semantic_checks.TextFacts(label_id=1)] * PER_LABEL
    facts += [semantic_checks.TextFacts(label_id=2)] * PER_LABEL
    facts.append(semantic_checks.TextFacts(label_id=1))
    facts.append(semantic_checks.TextFacts(label_id=1))

    # 실행
    found = semantic_checks.analyze(np.array(vectors, dtype=np.float32), facts)

    # 확인
    assert [(pair.a, pair.b) for pair in found.pairs] == [(0, 2 * PER_LABEL + 1)]
    assert [(s.index, s.label_id, s.suggested_label_id) for s in found.suspects] == [
        (2 * PER_LABEL, 1, 2)
    ]
    assert found.suspects[0].label_probability < semantic_checks.SUSPECT_MAX_LABEL_PROBABILITY


def test_analyze_leaves_labels_with_too_few_texts_unjudged():
    # 준비: 라벨 3은 문장이 2개뿐이다.
    vectors = [_vector(0, own) for own in range(6)] + [_vector(1, 6 + own) for own in range(6)]
    vectors += [_vector(0, 12 + own) for own in range(2)]
    labels = [1] * 6 + [2] * 6 + [3] * 2
    facts = [semantic_checks.TextFacts(label_id=label) for label in labels]

    # 실행
    found = semantic_checks.analyze(np.array(vectors, dtype=np.float32), facts)

    # 확인
    assert found.unjudged_label_ids == [3]
    assert all(suspect.label_id != 3 for suspect in found.suspects)


def test_analyze_marks_label_skewed_when_one_topic_dominates():
    # 준비: 라벨 1은 60%가 한 곳에 몰리고, 라벨 2는 고르게 퍼진다.
    rng = np.random.default_rng(3)
    crowd = np.zeros(DIM)
    crowd[0] = 1.0
    skewed = [(crowd + rng.normal(scale=0.01, size=DIM)).tolist() for _ in range(30)]
    skewed += [rng.normal(size=DIM).tolist() for _ in range(20)]
    spread = [rng.normal(size=DIM).tolist() for _ in range(50)]
    facts = [semantic_checks.TextFacts(label_id=1)] * 50
    facts += [semantic_checks.TextFacts(label_id=2)] * 50

    # 실행
    found = semantic_checks.analyze(np.array(skewed + spread, dtype=np.float32), facts)

    # 확인
    by_label = {skew.label_id: skew for skew in found.skews}
    assert by_label[1].is_skewed and by_label[1].largest_share >= 0.6
    assert not by_label[2].is_skewed
    assert all(index < 30 for index in by_label[1].example_indices)


# ---------- 뜻 분석 작업 ----------


async def test_build_map_saves_near_duplicates_suspects_and_summary(
    client, db_session, make_dataset
):
    # 준비 · 실행
    jev = _confirming_jev()
    made, state = await _analyzed(db_session, make_dataset, jev=jev)

    # 확인
    assert state.map is not None and state.checks is not None
    checks = state.checks
    assert checks.near_duplicates == {"pairs": 1, "texts": 2, "label_mismatch": 0}
    assert (checks.suspects.found, checks.suspects.open, checks.suspects.confirmed_open) == (
        1,
        1,
        1,
    )
    assert checks.jev == {"status": "judged", "detail": "1건"}
    assert jev.asked == [MISLABELED]  # type: ignore[attr-defined]
    response = (await client.get(f"{API}/datasets/{made.dataset.id}/map")).json()
    assert response["checks"]["suspects"]["confirmed_open"] == 1
    assert response["checks"]["thresholds"]["near_duplicate_similarity"] == 0.92


async def test_get_map_grades_semantic_checks_and_counts_open_suspects_by_label(
    client, db_session, make_dataset
):
    # 준비: 오라벨 의심 1건(문장 42개 중 2.4%) · 라벨이 같은 근접 중복 1쌍 · 쏠림을 잴 만큼 큰 라벨 없음
    made, _state = await _analyzed(db_session, make_dataset)

    # 실행
    checks = (await client.get(f"{API}/datasets/{made.dataset.id}/map")).json()["checks"]

    # 확인
    # 라벨이 같은 근접 중복은 좋음이다(라벨이 다른 쌍이 있을 때만 주의).
    assert checks["grades"] == {"suspect": "warn", "near_duplicate": "good", "skew": "good"}
    assert checks["suspects"]["open_by_label"] == {str(made.labels["가"].id): 1}
    assert checks["suspects"]["open_rate"] == pytest.approx(1 / (2 * PER_LABEL + 2))
    assert checks["thresholds"]["suspect_good_rate"] == semantic_checks.SUSPECT_GOOD_BELOW_RATE


async def test_get_map_grades_suspect_good_when_all_suspects_are_decided(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    await client.post(
        f"{API}/datasets/{made.dataset.id}/suspects/{_hash_of(made, MISLABELED)}/keep"
    )

    # 실행
    checks = (await client.get(f"{API}/datasets/{made.dataset.id}/map")).json()["checks"]

    # 확인
    assert checks["grades"]["suspect"] == "good"
    assert checks["suspects"]["open_by_label"] == {}


async def test_build_map_keeps_suspects_without_jev_when_jev_is_not_connected(
    db_session, make_dataset
):
    # 실행
    _made, state = await _analyzed(db_session, make_dataset)

    # 확인
    assert state.checks is not None
    assert state.checks.jev == {"status": "not_connected", "detail": "미연결"}
    assert (state.checks.suspects.found, state.checks.suspects.confirmed_open) == (1, 0)


async def test_build_map_finishes_with_jev_failure_noted_when_jev_is_down(db_session, make_dataset):
    # 실행
    _made, state = await _analyzed(db_session, make_dataset, jev=refused_transport())

    # 확인
    assert state.map is not None and state.checks is not None
    assert state.checks.jev["status"] == "failed"
    assert "연결 거부" in state.checks.jev["detail"]
    assert state.checks.suspects.found == 1


async def test_build_map_carries_kept_suspect_into_next_analysis(client, db_session, make_dataset):
    # 준비: 의심을 유지한 뒤 다시 분석한다.
    made, _state = await _analyzed(db_session, make_dataset)
    text_hash = _hash_of(made, MISLABELED)
    await client.post(f"{API}/datasets/{made.dataset.id}/suspects/{text_hash}/keep")

    # 실행
    state = await build_map(
        db_session, dataset_id=made.dataset.id, transport=_embedding_transport()
    )

    # 확인: 새 분석에서도 유지로 남아 대기 목록에 다시 나오지 않는다.
    assert state.checks is not None
    assert (state.checks.suspects.open, state.checks.suspects.kept) == (0, 1)
    assert (
        await db_session.scalar(select(Map.id).where(Map.dataset_id == made.dataset.id))
        == state.map.id
    )


# ---------- 제안: 오라벨 의심 ----------


async def test_list_suspects_returns_suspect_with_classifier_and_jev_evidence(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset, jev=_confirming_jev())

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/suspects")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert body["total"] == 1
    item = body["items"][0]
    assert (item["text"], item["record_count"]) == (MISLABELED, 1)
    assert (item["label"]["name"], item["suggested_label"]["name"]) == ("가", "나")
    assert item["jev_label"]["name"] == "나"
    assert item["is_confirmed"] is True and item["decision"] is None


async def test_accept_suspect_changes_label_and_returns_409_when_decided_again(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    text_hash = _hash_of(made, MISLABELED)
    url = f"{API}/datasets/{made.dataset.id}/suspects/{text_hash}"

    # 실행
    first = await client.post(f"{url}/accept")
    second = await client.post(f"{url}/accept")

    # 확인
    assert first.json() == {"changed": 1}
    record = await db_session.scalar(
        select(Record).where(Record.text == MISLABELED).execution_options(populate_existing=True)
    )
    assert record is not None and record.label_id == made.labels["나"].id
    assert second.status_code == status.HTTP_409_CONFLICT
    assert second.json() == {"detail": "이미 판단한 제안입니다."}
    left = (await client.get(f"{API}/datasets/{made.dataset.id}/suspects")).json()
    assert left["total"] == 0


async def test_accept_suspect_uses_given_label_and_returns_422_for_other_dataset_label(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    other = await make_dataset("다른 데이터", [Row("딴 문장", "딴 라벨")])
    url = f"{API}/datasets/{made.dataset.id}/suspects/{_hash_of(made, MISLABELED)}/accept"

    # 실행
    foreign = await client.post(url, json={"label_id": other.labels["딴 라벨"].id})

    # 확인
    assert foreign.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert foreign.json() == {"detail": "이 데이터셋의 라벨이 아닙니다."}


async def test_accept_suspect_returns_404_when_suspect_or_analysis_is_missing(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    plain = await make_dataset("분석 없음", [Row("안녕", "인사")])

    # 실행
    unknown = await client.post(f"{API}/datasets/{made.dataset.id}/suspects/{'0' * 64}/accept")
    no_analysis = await client.post(f"{API}/datasets/{plain.dataset.id}/suspects/{'0' * 64}/keep")

    # 확인
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    assert unknown.json() == {"detail": "오라벨 의심을 찾을 수 없습니다."}
    assert no_analysis.status_code == status.HTTP_404_NOT_FOUND
    assert no_analysis.json() == {"detail": "뜻 분석이 없습니다. 먼저 만들어 주세요."}


async def test_accept_confirmed_suspects_accepts_only_jev_confirmed(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset, jev=_confirming_jev())

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/suspects/accept-confirmed")

    # 확인
    assert response.json() == {"changed": 1}
    suspect = await db_session.scalar(
        select(LabelSuspect).execution_options(populate_existing=True)
    )
    assert suspect is not None and suspect.decision == SuspectDecision.ACCEPTED


# ---------- 문제 거르기 · 근접 중복 목록 · 지도 표시 ----------


async def test_list_records_filters_near_duplicates_and_open_suspects(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    url = f"{API}/datasets/{made.dataset.id}/records"

    # 실행
    near = (await client.get(url, params={"problem": "near_duplicate"})).json()
    suspect = (await client.get(url, params={"problem": "suspect"})).json()

    # 확인
    assert sorted(item["text"] for item in near["items"]) == ["가 문장 0", NEAR_TWIN]
    assert [item["text"] for item in suspect["items"]] == [MISLABELED]


async def test_list_near_duplicates_returns_partner_with_similarity(
    client, db_session, make_dataset
):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)
    record_id = next(record.id for record in made.records if record.text == "가 문장 0")

    # 실행
    response = await client.get(f"{API}/records/{record_id}/near-duplicates")

    # 확인
    body = response.json()
    assert [(item["text"], item["label_name"]) for item in body] == [(NEAR_TWIN, "가")]
    assert body[0]["similarity"] > 0.99


async def test_list_map_points_marks_near_duplicates_and_suspects(client, db_session, make_dataset):
    # 준비
    made, _state = await _analyzed(db_session, make_dataset)

    # 실행
    body = (await client.get(f"{API}/datasets/{made.dataset.id}/map/points")).json()

    # 확인
    flags = dict(zip(body["record_ids"], body["flags"], strict=True))
    by_text = {record.text: record.id for record in made.records}
    assert flags[by_text["가 문장 0"]] & semantic_map.FLAG_NEAR_DUPLICATE
    assert flags[by_text[MISLABELED]] & semantic_map.FLAG_SUSPECT
    assert not flags[by_text["나 문장 3"]] & (
        semantic_map.FLAG_NEAR_DUPLICATE | semantic_map.FLAG_SUSPECT
    )
