"""오답 찾기 테스트: 오답 풀만큼 · 순위 범위 · 정답과 같은 중복 묶음 빼기 · Jev가 답이라 한 문서 빼기 · 다시 찾기 ·
문서 풀이 늘면 스스로 다시 찾기 · 오답 훑기 · API."""

from fastapi import status
from sqlalchemy import func, select

from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.jobs import run_mine_job, run_negative_scan_job
from dent.modules.retrieval.mining import SCAN_BUCKETS, SCAN_MARGINS
from dent.modules.retrieval.models import Document, Judgment, JudgmentSource
from dent.system import jobs
from dent.system.models import JobStatus
from tests.modules.retrieval.helpers import connect_embedding, connect_jev, contains_jev, import_csv
from tests.system.helpers import vector_transport

API = "/api/v1/retrieval"

DOCS = [f"코퍼스 문서 {index}: 서로 다른 내용 {index * 7}" for index in range(30)]


async def _dataset(client, db_session) -> int:
    finished = await import_csv(
        db_session,
        header=["query", "positive"],
        # 질의 글을 정답 글과 같게 두어 정답이 가장 가깝게 한다(거짓 오답 경계에 걸리는 문서가 없게).
        rows=[[DOCS[0], DOCS[0]], [DOCS[1], DOCS[1]]],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
    )
    await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in DOCS[2:]],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=finished.dataset_id,
    )
    await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings",
        json={
            "negatives": 3,
            "negative_pool": 3,
            "mine_rank_from": 1,
            "mine_rank_to": 30,
            "mine_margin": 1.0,
        },
    )
    return finished.dataset_id


async def _mine(client, db_session, dataset_id: int, *, remine: bool = False, jev=None) -> int:
    response = await client.post(f"{API}/datasets/{dataset_id}/{'remine' if remine else 'mine'}")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    job = await jobs.get_job(db_session, job_id=response.json()["job_id"])
    await run_mine_job(job, transport=vector_transport(dim=16), jev_transport=jev)
    return job.id


async def _mined(db_session) -> list[Judgment]:
    return list(
        await db_session.scalars(
            select(Judgment)
            .where(Judgment.source == JudgmentSource.MINED.value)
            .execution_options(populate_existing=True)
        )
    )


async def test_mine_adds_negatives_up_to_pool(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)

    # 실행
    job_id = await _mine(client, db_session, dataset_id)

    # 확인
    job = await jobs.get_job(db_session, job_id=job_id)
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE
    assert job.result["added"] == 6
    mined = await _mined(db_session)
    assert len(mined) == 6
    assert all(judgment.grade == 0 for judgment in mined)
    positives = {
        (judgment.query_id, judgment.document_id)
        for judgment in await db_session.scalars(select(Judgment).where(Judgment.grade >= 1))
    }
    assert not positives & {(judgment.query_id, judgment.document_id) for judgment in mined}


async def test_mine_skips_documents_jev_calls_answers(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)
    await connect_jev(db_session)

    # 실행
    job_id = await _mine(client, db_session, dataset_id, jev=contains_jev(0.95))

    # 확인
    job = await jobs.get_job(db_session, job_id=job_id)
    await db_session.refresh(job)
    assert job.result["added"] == 0
    assert job.result["jev_rejected"] > 0


async def test_remine_replaces_mined_negatives(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)
    await _mine(client, db_session, dataset_id)
    await client.patch(
        f"{API}/datasets/{dataset_id}/settings", json={"negatives": 1, "negative_pool": 1}
    )

    # 실행
    await _mine(client, db_session, dataset_id, remine=True)

    # 확인
    count = await db_session.scalar(
        select(func.count())
        .select_from(Judgment)
        .where(Judgment.source == JudgmentSource.MINED.value)
    )
    assert count == 2


async def test_mine_returns_422_without_embedding(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/mine")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_mine_starts_earlier_in_small_corpus(client, db_session):
    # 준비: 문서 6개뿐이라 10~100위가 없다.
    finished = await import_csv(
        db_session,
        header=["query", "positive"],
        rows=[[DOCS[0], DOCS[0]]],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
    )
    await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in DOCS[1:6]],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=finished.dataset_id,
    )
    await connect_embedding(db_session)

    # 실행
    job_id = await _mine(client, db_session, finished.dataset_id)

    # 확인: 시작 순위를 3위(코퍼스의 절반)로 낮춰 3~6위에서 고른다.
    job = await jobs.get_job(db_session, job_id=job_id)
    await db_session.refresh(job)
    assert job.result["added"] == 4


async def test_mine_skips_documents_in_positive_duplicate_group(client, db_session):
    # 준비: 정답과 본문이 같은 문서(제목만 다름)가 코퍼스에 있다.
    finished = await import_csv(
        db_session,
        header=["query", "positive"],
        rows=[[DOCS[0], DOCS[0]]],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
    )
    await import_csv(
        db_session,
        header=["title", "text"],
        rows=[["다른 제목", DOCS[0]], *[["", text] for text in DOCS[1:25]]],
        mapping={"shape": "documents", "text": "text", "title": "title"},
        dataset_id=finished.dataset_id,
    )
    await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings",
        json={"negative_pool": 24, "mine_rank_from": 1, "mine_rank_to": 30, "mine_margin": 1.0},
    )
    await connect_embedding(db_session)
    same_body = await db_session.scalar(select(Document.id).where(Document.title == "다른 제목"))

    # 실행
    await _mine(client, db_session, finished.dataset_id)

    # 확인
    mined = await _mined(db_session)
    assert mined
    assert same_body not in {judgment.document_id for judgment in mined}


async def test_negative_scan_writes_cells_to_settings(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)
    await connect_jev(db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/negative-scan")
    job = await jobs.get_job(db_session, job_id=response.json()["job_id"])
    await run_negative_scan_job(
        job, transport=vector_transport(dim=16), jev_transport=contains_jev(0.9)
    )

    # 확인
    assert response.status_code == status.HTTP_201_CREATED
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE
    settings = (await client.get(f"{API}/datasets/{dataset_id}")).json()["settings"]
    cells = settings["negative_scan"]["cells"]
    assert len(cells) == len(SCAN_BUCKETS) * len(SCAN_MARGINS)
    asked = [cell for cell in cells if cell["asked"]]
    assert asked
    assert all(cell["false_negatives"] == cell["asked"] for cell in asked)


async def test_negative_scan_returns_422_without_jev(client, db_session):
    # 준비
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/negative-scan")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "Jev" in response.json()["detail"]


async def test_mine_again_for_all_queries_when_corpus_grew(client, db_session):
    # 준비: 모든 질의의 오답을 찾은 뒤 문서를 더한다(데이터 추가).
    dataset_id = await _dataset(client, db_session)
    await connect_embedding(db_session)
    await _mine(client, db_session, dataset_id)
    before = {(judgment.query_id, judgment.document_id) for judgment in await _mined(db_session)}
    await import_csv(
        db_session,
        header=["text"],
        rows=[[f"새 문서 {index}: 더한 내용 {index * 11}"] for index in range(5)],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=dataset_id,
    )
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    mine = next(move for move in overview.flow.moves if move.key == "mine")

    # 실행: 모자란 질의가 없어도 [오답 찾기]는 모든 질의를 다시 찾는다.
    job_id = await _mine(client, db_session, dataset_id)

    # 확인
    assert (mine.state, mine.text) == ("todo", "새 문서 5 · 다시 찾기")
    job = await jobs.get_job(db_session, job_id=job_id)
    await db_session.refresh(job)
    assert (job.result["queries"], job.result["new_documents"]) == (2, 5)
    assert len(before) == len(await _mined(db_session)) == 6
    assert await retrieval_service.count_unmined_documents(db_session, dataset_id=dataset_id) == 0
