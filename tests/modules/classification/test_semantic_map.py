"""의미 지도 테스트 (웹 없이): 만들기 세 단계, 캐시 다시 쓰기, 멈추기, 실패, 지도 이후 바뀜, 점의 표시.

임베딩 서버는 가짜(tests.system.helpers.vector_transport)를 쓰고, 좌표는 작은 데이터로 실제 UMAP을 돌린다.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from dent.modules.classification import records as records_service
from dent.modules.classification import semantic_map
from dent.modules.classification.jobs import fail_interrupted_map
from dent.modules.classification.models import Map, MapPoint, MapStatus
from dent.modules.classification.schemas import RecordBulkUpdate
from dent.system import embedding, jobs
from dent.system.exceptions import ConflictError, InvalidInputError
from dent.system.models import Embedding, Job, JobStatus
from dent.system.text import normalize_text
from tests.modules.classification.helpers import (
    EMBEDDING_MODEL,
    Row,
    build_map,
    connect_embedding,
    run_map,
)
from tests.system.helpers import refused_transport, vector_of, vector_transport

# 지도에 놓을 문장들. 넷째는 셋째와 띄어쓰기만 달라 같은 문장이고, 마지막은 휴지통에 있다.
ROWS = [
    Row("배송이 너무 늦어요", "배송"),
    Row("환불은 언제 되나요", "환불"),
    Row("상품이 망가져서 왔어요", "품질"),
    Row("상품이  망가져서 왔어요", "품질"),
    Row("포장 상태가 좋아요", "품질"),
    Row("버린 문장", "배송", trashed=True),
]


async def _points(db_session, *, map_id: int) -> dict[int, tuple[float, float]]:
    rows = await db_session.execute(
        select(MapPoint.record_id, MapPoint.x, MapPoint.y).where(MapPoint.map_id == map_id)
    )
    return {record_id: (x, y) for record_id, x, y in rows}


async def test_build_map_places_every_active_record_and_shares_coords_for_same_text(
    db_session, make_dataset
):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    transport = vector_transport()

    # 실행
    state = await build_map(db_session, dataset_id=made.dataset.id, transport=transport)

    # 확인
    assert state.map is not None and state.run is None
    assert state.map.status == MapStatus.DONE
    assert (state.map.text_count, state.map.point_count, state.map.record_count) == (4, 5, 5)
    assert state.map.params["method"] == "umap"
    assert not state.outdated
    points = await _points(db_session, map_id=state.map.id)
    ids = [record.id for record in made.records]
    assert set(points) == set(ids[:5])
    assert points[ids[2]] == points[ids[3]]
    assert all(-1.0 <= value <= 1.0 for point in points.values() for value in point)
    job = await jobs.get_job(db_session, job_id=state.map.job_id)
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE
    assert job.result is not None and job.result["embedded"] == 4


async def test_build_map_embeds_normalized_text_once_per_same_text(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    transport = vector_transport()

    # 실행
    await build_map(db_session, dataset_id=made.dataset.id, transport=transport)

    # 확인: 연결 확인 문장 하나 + 서로 다른 문장 넷 (휴지통 문장은 빠진다)
    embedded = [text for text in transport.inputs if text != embedding.PROBE_TEXT]
    assert sorted(embedded) == sorted(
        ["배송이 너무 늦어요", "환불은 언제 되나요", "상품이 망가져서 왔어요", "포장 상태가 좋아요"]
    )


async def test_build_map_reuses_cached_embeddings_and_keeps_one_map(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    first = await build_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())
    transport = vector_transport()

    # 실행
    second = await build_map(db_session, dataset_id=made.dataset.id, transport=transport)

    # 확인
    assert transport.inputs == []
    assert first.map is not None and second.map is not None
    assert second.map.id != first.map.id
    map_count = await db_session.scalar(select(func.count()).select_from(Map))
    embedding_count = await db_session.scalar(select(func.count()).select_from(Embedding))
    assert (map_count, embedding_count) == (1, 4)


async def test_build_map_stops_between_batches_when_cancel_is_requested(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    started = await semantic_map.start_map(db_session, dataset_id=made.dataset.id)
    assert started.run is not None and started.run.job_id is not None
    claimed = await jobs.claim_next_job(db_session)
    assert claimed is not None
    await semantic_map.cancel_map(db_session, dataset_id=made.dataset.id)
    transport = vector_transport()

    # 실행
    await run_map(db_session, dataset_id=made.dataset.id, transport=transport)

    # 확인
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)
    assert state.map is None
    assert state.run is not None and state.run.status == MapStatus.CANCELED
    job = await db_session.get(Job, started.run.job_id, populate_existing=True)
    assert job is not None and job.status == JobStatus.CANCELED
    assert await _points(db_session, map_id=started.run.id) == {}


async def test_build_map_sends_next_batch_before_saving_current(
    db_session, make_dataset, monkeypatch
):
    # 준비: 묶음을 문장 하나로 줄여 서로 다른 문장 넷이 묶음 넷이 되게 한다.
    monkeypatch.setattr(semantic_map, "EMBED_CHUNK", 1)
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    transport = vector_transport()
    save_vectors = embedding.save_vectors
    saved_chunks = 0

    def sent_chunks() -> int:
        return len([text for text in transport.inputs if text != embedding.PROBE_TEXT])

    async def save_after_next_is_sent(db, **kwargs):
        nonlocal saved_chunks
        saved_chunks += 1
        if saved_chunks < 4:
            # 다음 묶음 요청이 이 묶음을 저장하기 전에 나가야 한다. 안 나가면 1초 뒤 TimeoutError.
            async with asyncio.timeout(1):
                while sent_chunks() <= saved_chunks:
                    await asyncio.sleep(0.01)
        await save_vectors(db, **kwargs)

    monkeypatch.setattr(embedding, "save_vectors", save_after_next_is_sent)

    # 실행
    state = await build_map(db_session, dataset_id=made.dataset.id, transport=transport)

    # 확인: 묶음 넷이 모두 저장되고, 문장마다 자기 벡터가 붙었다.
    assert state.map is not None and state.map.status == MapStatus.DONE
    assert saved_chunks == 4
    texts = {record.text_hash: normalize_text(record.text) for record in made.records[:5]}
    model = await embedding.get_registered_model(db_session, name=EMBEDDING_MODEL)
    assert model is not None and len(texts) == 4
    loaded = await embedding.load_vectors(db_session, model=model, text_hashes=list(texts))
    for vector, text in zip(loaded, texts.values(), strict=True):
        assert vector.tolist() == pytest.approx(vector_of(text, dim=8), abs=1e-2)


async def test_build_map_keeps_saved_batches_when_canceled_midway(
    db_session, make_dataset, monkeypatch
):
    # 준비: 묶음 넷 가운데 첫 묶음을 저장한 직후 사용자가 멈추기를 누른 것처럼 한다.
    monkeypatch.setattr(semantic_map, "EMBED_CHUNK", 1)
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    started = await semantic_map.start_map(db_session, dataset_id=made.dataset.id)
    assert started.run is not None and started.run.job_id is not None
    job_id = started.run.job_id
    save_vectors = embedding.save_vectors

    async def save_then_cancel(db, **kwargs):
        await save_vectors(db, **kwargs)
        await jobs.request_cancel(db, job_id=job_id)

    monkeypatch.setattr(embedding, "save_vectors", save_then_cancel)

    # 실행
    await run_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())

    # 확인: 저장한 첫 묶음만 캐시에 남고, 미리 보낸 다음 묶음의 답은 버렸다.
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)
    assert state.map is None
    assert state.run is not None and state.run.status == MapStatus.CANCELED
    job = await db_session.get(Job, job_id, populate_existing=True)
    assert job is not None and job.status == JobStatus.CANCELED
    embedding_count = await db_session.scalar(select(func.count()).select_from(Embedding))
    assert embedding_count == 1


async def test_build_map_records_connection_phases_and_retries_in_job_events(
    db_session, make_dataset, monkeypatch
):
    # 준비: 모델을 미리 등록해 확인 요청 없이 곧장 임베딩을 보내고, 그 첫 요청은 503으로 실패시킨다.
    monkeypatch.setattr(embedding, "EMBEDDING_RETRY_BASE_S", 0)
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=8)
    await db_session.commit()
    await semantic_map.start_map(db_session, dataset_id=made.dataset.id)
    # 작업 실행기처럼 먼저 꺼낸다(꺼낼 때 '시작' 사건이 남는다).
    assert await jobs.claim_next_job(db_session) is not None

    # 실행
    await run_map(db_session, dataset_id=made.dataset.id, transport=vector_transport(fail_first=1))

    # 확인
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)
    assert state.map is not None and state.map.status == MapStatus.DONE
    job = await db_session.get(Job, state.map.job_id, populate_existing=True)
    assert job is not None
    assert (job.dataset_id, job.dataset_name) == (made.dataset.id, "지도")
    kinds = [(event["type"], event.get("phase")) for event in job.events]
    assert kinds == [
        ("start", None),
        ("connection", None),
        ("phase", "embedding"),
        ("retry", None),
        ("phase", "projecting"),
        ("phase", "analyzing"),
        ("phase", "saving"),
    ]
    retry = job.events[3]
    assert (retry["role"], retry["attempt"]) == ("embedding", 1)
    assert "503" in retry["reason"]
    assert job.events[1]["model"] == EMBEDDING_MODEL


async def test_cancel_map_stops_queued_map_at_once(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await semantic_map.start_map(db_session, dataset_id=made.dataset.id)

    # 실행
    state = await semantic_map.cancel_map(db_session, dataset_id=made.dataset.id)

    # 확인
    assert state.run is not None
    assert state.run.status == MapStatus.CANCELED
    assert await jobs.claim_next_job(db_session) is None


async def test_run_map_job_fails_map_with_message_when_embedding_server_is_down(
    db_session, make_dataset, monkeypatch
):
    # 준비
    monkeypatch.setattr(embedding, "EMBEDDING_RETRY_BASE_S", 0)
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await embedding.register_model(db_session, name="test-embedding", dim=8)
    await semantic_map.start_map(db_session, dataset_id=made.dataset.id)

    # 실행
    await run_map(db_session, dataset_id=made.dataset.id, transport=refused_transport())

    # 확인
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)
    assert state.run is not None
    assert state.run.status == MapStatus.FAILED
    assert state.run.error is not None and "연결 거부" in state.run.error
    job = await db_session.get(Job, state.run.job_id, populate_existing=True)
    assert job is not None and job.status == JobStatus.FAILED
    assert job.error == state.run.error


async def test_failed_rebuild_keeps_previous_map(db_session, make_dataset, monkeypatch):
    # 준비
    monkeypatch.setattr(embedding, "EMBEDDING_RETRY_BASE_S", 0)
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    first = await build_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())
    await records_service.bulk_update(
        db_session,
        dataset_id=made.dataset.id,
        data=RecordBulkUpdate(record_ids=[made.records[0].id], action="trash"),
    )
    await db_session.execute(Embedding.__table__.delete())
    await db_session.commit()

    # 실행
    second = await build_map(db_session, dataset_id=made.dataset.id, transport=refused_transport())

    # 확인
    assert first.map is not None and second.map is not None
    assert second.map.id == first.map.id
    assert second.run is not None and second.run.status == MapStatus.FAILED
    assert len(await _points(db_session, map_id=first.map.id)) == 5


async def test_fail_interrupted_map_marks_map_failed(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    started = await semantic_map.start_map(db_session, dataset_id=made.dataset.id)
    assert started.run is not None and started.run.job_id is not None
    job = await jobs.get_job(db_session, job_id=started.run.job_id)

    # 실행
    await fail_interrupted_map(job)

    # 확인
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)
    assert state.run is not None and state.run.status == MapStatus.FAILED
    assert state.run.error is not None and "끊겨" in state.run.error


async def test_start_map_fails_when_embedding_is_not_connected(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)

    # 실행 · 확인
    with pytest.raises(InvalidInputError, match="임베딩 미연결"):
        await semantic_map.start_map(db_session, dataset_id=made.dataset.id)


async def test_start_map_fails_when_dataset_has_no_active_records(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", [Row("버린 문장", "배송", trashed=True)])
    await connect_embedding(db_session)

    # 실행 · 확인
    with pytest.raises(InvalidInputError):
        await semantic_map.start_map(db_session, dataset_id=made.dataset.id)


async def test_start_map_fails_when_map_is_already_building(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await semantic_map.start_map(db_session, dataset_id=made.dataset.id)

    # 실행 · 확인
    with pytest.raises(ConflictError):
        await semantic_map.start_map(db_session, dataset_id=made.dataset.id)


async def test_cancel_map_fails_when_nothing_is_building(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)

    # 실행 · 확인
    with pytest.raises(ConflictError):
        await semantic_map.cancel_map(db_session, dataset_id=made.dataset.id)


async def test_get_map_state_is_outdated_after_records_change(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await build_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())

    # 실행
    await records_service.bulk_update(
        db_session,
        dataset_id=made.dataset.id,
        data=RecordBulkUpdate(record_ids=[made.records[0].id], action="exclude"),
    )
    state = await semantic_map.get_map_state(db_session, dataset_id=made.dataset.id)

    # 확인
    assert state.outdated


async def test_list_map_points_returns_current_labels_and_flags(db_session, make_dataset):
    # 준비
    rows = [
        Row("같은 문장", "배송"),
        Row("같은 문장", "환불"),
        Row("겹친 문장", "배송"),
        Row("겹친 문장", "배송"),
        Row("짧음", "환불", exclude_reason="manual"),
        Row("그대로 두는 문장", "환불"),
    ]
    made = await make_dataset("지도", rows)
    await connect_embedding(db_session)
    await build_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())

    # 실행
    points = await semantic_map.list_map_points(db_session, dataset_id=made.dataset.id)

    # 확인
    flags = dict(zip(points.record_ids, points.flags, strict=True))
    ids = [record.id for record in made.records]
    conflict, duplicate = semantic_map.FLAG_CONFLICT, semantic_map.FLAG_DUPLICATE
    short, excluded = semantic_map.FLAG_SHORT, semantic_map.FLAG_EXCLUDED
    assert [flags[record_id] for record_id in ids] == [
        conflict,
        conflict,
        duplicate,
        duplicate,
        short + excluded,
        0,
    ]
    assert [label.name for label in points.labels] == ["배송", "환불"]
    assert points.label_ids[0] == made.labels["배송"].id
    assert len(points.x) == len(points.y) == len(points.record_ids) == 6


async def test_list_map_points_leaves_out_records_trashed_after_map(db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", ROWS)
    await connect_embedding(db_session)
    await build_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())
    await records_service.bulk_update(
        db_session,
        dataset_id=made.dataset.id,
        data=RecordBulkUpdate(record_ids=[made.records[0].id], action="trash"),
    )

    # 실행
    points = await semantic_map.list_map_points(db_session, dataset_id=made.dataset.id)

    # 확인
    assert made.records[0].id not in points.record_ids
    assert len(points.record_ids) == 4
