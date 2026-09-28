"""검색 뜻 분석 테스트: 만들기 · 상태 · 멈추기 · 지도 점 · 기준 검색 순위 · 기준 검색 점수 · 제안(수락 · 유지)."""

from fastapi import status
from sqlalchemy import func, select

from dent.modules.retrieval.models import (
    Analysis,
    Judgment,
    MapPoint,
    Query,
    Ranking,
    Suggestion,
    SuggestionKind,
)
from tests.modules.retrieval.helpers import (
    build_analysis,
    connect_embedding,
    connect_jev,
    contains_jev,
    import_csv,
)
from tests.system.helpers import vector_transport

API = "/api/v1/retrieval"

# 문서 12개 · 질의 6개 (질의마다 정답 1 · 오답 1). 평가(test) 질의 2개.
DOCS = [
    f"문서 {index}번의 본문: 주제 {index % 4}에 관한 설명과 사실 {index}" for index in range(12)
]
ROWS = [
    [
        f"질의 {index} 주제 {index % 4}",
        DOCS[index],
        DOCS[(index + 6) % 12],
    ]
    for index in range(6)
]
MAPPING = {
    "shape": "triplet",
    "query": "query",
    "positive": "positive",
    "negatives": ["negative"],
}


async def _dataset(db_session) -> int:
    finished = await import_csv(
        db_session,
        header=["query", "positive", "negative"],
        rows=ROWS,
        mapping=MAPPING,
    )
    # 판정 없는 문서도 코퍼스에 둔다(질의 없는 청크).
    await import_csv(
        db_session,
        header=["query", "positive", "negative"],
        rows=[[f"여분 질의 {index}", DOCS[6 + index], ""] for index in range(6)],
        mapping=MAPPING,
        dataset_id=finished.dataset_id,
    )
    return finished.dataset_id


async def test_start_analysis_returns_422_without_embedding(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/map")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "임베딩 미연결: 연결 설정에서 임베딩 서버를 연결하세요."}


async def test_build_analysis_makes_points_rankings_and_kpi(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)

    # 실행
    state = await build_analysis(
        db_session, dataset_id=dataset_id, transport=vector_transport(dim=16)
    )

    # 확인
    assert state.done is not None
    assert state.outdated is False
    assert state.model == "test-embedding"
    done = state.done
    points = await db_session.scalar(
        select(func.count()).select_from(MapPoint).where(MapPoint.analysis_id == done.id)
    )
    assert points == done.query_count + done.document_count
    queries = await db_session.scalar(select(func.count()).select_from(Query))
    top = await db_session.scalar(
        select(func.count())
        .select_from(Ranking)
        .where(Ranking.analysis_id == done.id, Ranking.rank <= 10)
    )
    assert top == queries * 10
    # 분할이 없으므로 정답이 있는 질의 모두(12)로 잰다.
    assert done.checks["kpi"]["evaluated"] == queries
    assert 0 <= done.checks["kpi"]["recall_at_10"] <= 1
    assert done.checks["topics"]["clusters"] == 8
    response = await client.get(f"{API}/datasets/{dataset_id}/overview")
    assert response.json()["kpi"]["baseline_recall_at_10"] == done.checks["kpi"]["recall_at_10"]


async def test_build_analysis_for_documents_only_dataset(db_session):
    # 준비: 질의가 없는 문서만 데이터셋
    finished = await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in DOCS[:6]],
        mapping={"shape": "documents", "text": "text"},
    )
    await connect_embedding(db_session)

    # 실행
    state = await build_analysis(
        db_session, dataset_id=finished.dataset_id, transport=vector_transport(dim=16)
    )

    # 확인
    assert state.done is not None
    assert state.done.query_count == 0
    assert state.done.checks["kpi"] is None
    assert state.done.checks["topics"]["clusters"] == 6
    assert len(state.done.checks["topics"]["empty"]) == 6


async def test_map_points_lists_documents_then_queries(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/map/points")

    # 확인
    body = response.json()
    assert body["kinds"][0] == "document"
    assert body["kinds"][-1] == "query"
    assert len(body["ids"]) == len(body["x"]) == len(body["flags"]) == 24
    assert body["flag_names"][0] == "no_positive"


async def test_map_matches_and_text(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    matches = await client.get(f"{API}/datasets/{dataset_id}/map/matches", params={"q": "주제 1"})
    query_id = matches.json()["query_ids"][0]
    text = await client.get(
        f"{API}/datasets/{dataset_id}/map/text", params={"kind": "query", "item_id": query_id}
    )

    # 확인
    assert matches.json()["document_ids"]
    assert "주제 1" in text.json()["text"]


async def test_query_detail_has_ranking_after_analysis(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))
    query_id = await db_session.scalar(select(Query.id).order_by(Query.id))

    # 실행
    response = await client.get(f"{API}/queries/{query_id}")

    # 확인
    body = response.json()
    assert body["has_ranking"] is True
    assert [item["rank"] for item in body["ranked"]] == list(range(1, 11))


async def test_start_analysis_returns_409_while_building(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{dataset_id}/map")

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/map")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT


async def test_cancel_queued_analysis(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{dataset_id}/map")

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/map/cancel")

    # 확인
    assert response.json()["run"]["status"] == "canceled"


async def test_jev_confirms_suggestions(db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await connect_jev(db_session)

    # 실행
    state = await build_analysis(
        db_session,
        dataset_id=dataset_id,
        transport=vector_transport(dim=16),
        jev_transport=contains_jev(0.95),
    )

    # 확인
    assert state.done is not None
    assert state.done.checks["jev"]["status"] == "judged"
    suggestions = list(await db_session.scalars(select(Suggestion)))
    kinds = {suggestion.kind for suggestion in suggestions}
    # Jev가 늘 '예'라서 정답 의심은 확인되지 않고, 거짓 오답 · 빠진 정답만 확인된다.
    for suggestion in suggestions:
        expected = suggestion.kind != SuggestionKind.SUSPECT_POSITIVE.value
        assert suggestion.is_confirmed is expected
    assert SuggestionKind.MISSING_POSITIVE.value in kinds


async def _suggestion(
    db_session, dataset_id: int, kind: SuggestionKind, *, confirmed: bool
) -> Suggestion:
    """다 만든 분석에 제안 하나를 직접 넣는다(분석의 계산과 상관없이 수락 · 유지를 보려고)."""
    analysis_id = await db_session.scalar(
        select(Analysis.id).where(Analysis.dataset_id == dataset_id, Analysis.status == "done")
    )
    judgment = await db_session.scalar(
        select(Judgment)
        .where(Judgment.dataset_id == dataset_id, Judgment.grade == 0)
        .order_by(Judgment.query_id)
    )
    suggestion = Suggestion(
        analysis_id=analysis_id,
        kind=kind.value,
        query_id=judgment.query_id,
        document_id=judgment.document_id,
        rank=2,
        similarity=0.9,
        jev_probability=0.93,
        is_confirmed=confirmed,
    )
    db_session.add(suggestion)
    await db_session.commit()
    return suggestion


async def test_accept_false_negative_makes_positive(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))
    await db_session.execute(Suggestion.__table__.delete())
    await db_session.commit()
    suggestion = await _suggestion(
        db_session, dataset_id, SuggestionKind.FALSE_NEGATIVE, confirmed=True
    )

    # 실행
    response = await client.post(f"{API}/suggestions/{suggestion.id}/accept")
    again = await client.post(f"{API}/suggestions/{suggestion.id}/accept")

    # 확인
    assert response.json() == {"changed": 1}
    judgment = await db_session.get(
        Judgment, (suggestion.query_id, suggestion.document_id), populate_existing=True
    )
    assert (judgment.grade, judgment.source) == (1, "human")
    assert again.status_code == status.HTTP_409_CONFLICT


async def test_keep_and_accept_confirmed_suggestions(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))
    await db_session.execute(Suggestion.__table__.delete())
    await db_session.commit()
    kept = await _suggestion(db_session, dataset_id, SuggestionKind.FALSE_NEGATIVE, confirmed=False)

    # 실행
    keep = await client.post(f"{API}/suggestions/{kept.id}/keep")
    page = await client.get(f"{API}/datasets/{dataset_id}/suggestions", params={"decided": True})
    accepted = await client.post(f"{API}/datasets/{dataset_id}/suggestions/accept-confirmed")

    # 확인
    assert keep.json() == {"changed": 0}
    assert [item["decision"] for item in page.json()["items"]] == ["kept"]
    assert accepted.json() == {"changed": 0}


async def test_list_suggestions_returns_404_without_analysis(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/suggestions")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_hard_negative_rate_ignores_negatives_added_after_analysis(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))
    before = await client.get(f"{API}/datasets/{dataset_id}/overview")
    query_id = await db_session.scalar(select(Query.id).order_by(Query.id))
    unused = await db_session.scalar(
        select(Judgment.document_id)
        .where(Judgment.query_id != query_id)
        .order_by(Judgment.document_id.desc())
    )

    # 실행: 분석 뒤에 오답 하나를 더한다(순위가 없다).
    await client.put(f"{API}/queries/{query_id}/judgments/{unused}", json={"grade": 0})
    after = await client.get(f"{API}/datasets/{dataset_id}/overview")

    # 확인
    def easy(response):
        return next(check for check in response.json()["checks"] if check["key"] == "easy_negative")

    assert easy(after)["value"] == easy(before)["value"]


async def test_first_rank_counts_queries_added_after_analysis_apart_from_no_positive(
    client, db_session
):
    # 준비: 분석 뒤에 정답이 있는 질의 셋을 더 가져온다(분석에 순위가 없다).
    dataset_id = await _dataset(db_session)
    await connect_embedding(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, transport=vector_transport(dim=16))
    await import_csv(
        db_session,
        header=["query", "positive", "negative"],
        rows=[[f"분석 뒤 질의 {index}", DOCS[index], ""] for index in range(3)],
        mapping=MAPPING,
        dataset_id=dataset_id,
    )

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/overview")

    # 확인: 정답이 있는 새 질의는 '정답 없음'이 아니라 '분석에 없음'(흐림)이다.
    bins = {item["label"]: item for item in response.json()["first_rank"]}
    assert bins["정답 없음"]["count"] == 0
    assert (bins["분석에 없음"]["count"], bins["분석에 없음"]["tone"]) == (3, "muted")
    ranked = sum(
        item["count"] for label, item in bins.items() if label not in ("정답 없음", "분석에 없음")
    )
    assert ranked == 12
