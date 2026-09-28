"""검색 가져오기 테스트: 모양마다 줄 규칙, 합치기, 판정 충돌, 다시 돌리기, 필드 맞춤 오류."""

import pytest
from sqlalchemy import func, select

from dent.modules.retrieval import edits, importing
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.jobs import run_import_job
from dent.modules.retrieval.models import DatasetSettings, Document, Judgment, Query
from dent.modules.retrieval.schemas import ImportCreate
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import ImportStatus, JobStatus
from tests.modules.retrieval.conftest import TRIPLET_HEADER, TRIPLET_MAPPING, TRIPLET_ROWS
from tests.modules.retrieval.helpers import csv_bytes, import_csv
from tests.system.helpers import upload


async def _count(db_session, model, dataset_id: int) -> int:
    return await db_session.scalar(
        select(func.count()).select_from(model).where(model.dataset_id == dataset_id)
    )


async def test_import_triplets_merges_documents_and_keeps_negatives(db_session, import_triplets):
    # 준비 · 실행
    finished = await import_triplets()

    # 확인
    assert finished.status == ImportStatus.DONE
    assert finished.rows_added == 3
    dataset_id = finished.dataset_id
    assert await _count(db_session, Query, dataset_id) == 3
    # 정답 3 + 오답 2(배송 · 회원) = 5. 두 번째 줄의 오답은 첫 줄의 정답과 같아 합친다.
    assert await _count(db_session, Document, dataset_id) == 5
    grades = (
        await db_session.execute(
            select(Judgment.grade, func.count())
            .where(Judgment.dataset_id == dataset_id)
            .group_by(Judgment.grade)
            .order_by(Judgment.grade)
        )
    ).all()
    assert grades == [(0, 4), (1, 3)]
    assert finished.result["documents_added"] == 5
    assert finished.result["conflicts"] == 0
    settings = await db_session.get(DatasetSettings, dataset_id)
    assert settings is not None and settings.shape == "triplet"


async def test_import_documents_only_skips_empty_text(db_session):
    # 준비 · 실행
    finished = await import_csv(
        db_session,
        header=["id", "title", "text"],
        rows=[
            ["d1", "환불", "환불 규정 본문"],
            ["d2", "빈 문서", ""],
            ["d3", "환불", "환불 규정 본문"],
        ],
        mapping={"shape": "documents", "text": "text", "title": "title", "doc_key": "id"},
    )

    # 확인
    assert finished.status == ImportStatus.DONE
    assert finished.skipped_lines == [{"line": 3, "reason": importing.SKIP_EMPTY_DOCUMENT}]
    documents = (await db_session.scalars(select(Document).order_by(Document.id))).all()
    assert [(document.doc_key, document.title) for document in documents] == [("d1", "환불")]
    assert finished.result["documents_merged"] == 1
    assert await _count(db_session, Query, finished.dataset_id) == 0


async def test_import_scored_maps_scores_to_grades_and_marks_conflicts(db_session):
    # 준비 · 실행
    finished = await import_csv(
        db_session,
        header=["query", "doc", "score"],
        rows=[
            ["환불 기간", "환불은 3일", "0.9"],
            ["환불 기간", "배송은 2일", "0"],
            ["환불 기간", "배송은 2일", "1"],
            ["환불 기간", "회원 가입", "2"],
            ["환불 기간", "알 수 없음", "높음"],
        ],
        mapping={"shape": "scored", "query": "query", "document": "doc", "score": "score"},
    )

    # 확인
    assert finished.rows_added == 4
    assert finished.skipped_lines == [{"line": 6, "reason": importing.SKIP_BAD_SCORE}]
    rows = (
        await db_session.execute(
            select(Document.text, Judgment.grade, Judgment.conflict)
            .join(Document, Document.id == Judgment.document_id)
            .order_by(Document.id)
        )
    ).all()
    assert rows == [("환불은 3일", 1, False), ("배송은 2일", 1, True), ("회원 가입", 2, False)]
    assert finished.result["conflicts"] == 1


async def test_import_mrc_keeps_first_answer(db_session):
    # 준비 · 실행
    await import_csv(
        db_session,
        header=["question", "context", "answers"],
        rows=[
            ["수도는?", "대한민국의 수도는 서울이다.", '{"text": ["서울"], "answer_start": [10]}']
        ],
        mapping={"shape": "mrc", "query": "question", "positive": "context", "answer": "answers"},
    )

    # 확인
    query = await db_session.scalar(select(Query))
    assert query is not None and query.answer == "서울"


async def test_import_merges_same_query_on_second_import(db_session, import_triplets):
    # 준비
    first = await import_triplets()

    # 실행: 원본 분할이 달라도(test) 같은 질의로 합친다.
    second = await import_csv(
        db_session,
        header=["query", "positive", "negative_1", "negative_2", "split"],
        rows=[["환불은 며칠 걸리나요", "환불 신청은 마이페이지에서 합니다.", "", "", "test"]],
        mapping=TRIPLET_MAPPING,
        dataset_id=first.dataset_id,
    )

    # 확인
    assert second.status == ImportStatus.DONE
    assert second.result["queries_added"] == 0
    assert await _count(db_session, Query, first.dataset_id) == 3
    refund = await db_session.scalar(select(Query).where(Query.text == "환불은 며칠 걸리나요"))
    positives = await db_session.scalar(
        select(func.count()).where(Judgment.query_id == refund.id, Judgment.grade >= 1)
    )
    assert positives == 2


async def test_import_keeps_first_shape_when_adding_other_shape(db_session, import_triplets):
    # 준비: 세 쌍(입구 3)으로 만든 데이터셋
    first = await import_triplets()

    # 실행: 문서만 모양을 더한다(데이터 추가).
    second = await import_csv(
        db_session,
        header=["text"],
        rows=[["교환은 받은 날부터 7일 안에 신청합니다."]],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=first.dataset_id,
    )

    # 확인: 문서는 더하고, 입구는 처음 가져온 모양 그대로다.
    assert second.status == ImportStatus.DONE
    assert second.result["documents_added"] == 1
    settings = await db_session.get(DatasetSettings, first.dataset_id, populate_existing=True)
    assert settings is not None and settings.shape == "triplet"
    assert retrieval_service.entry_stage(settings) == 3


# 청크로 나눌 만큼 긴 지문: 문장마다 번호가 달라 답이 든 청크를 가를 수 있다.
LONG_PASSAGE = " ".join(
    f"{index}번째 문장은 환불 규정의 세부 내용을 적은 글입니다." for index in range(200)
)
MRC_MAPPING = {"shape": "mrc", "query": "q", "positive": "p", "answer": "a"}


async def test_import_routes_split_passage_to_answer_chunk_when_adding(db_session):
    # 준비: 긴 지문을 청크로 나눈 데이터셋
    first = await import_csv(
        db_session,
        header=["q", "p", "a"],
        rows=[["환불 규정의 첫 문장은?", LONG_PASSAGE, "0번째 문장"]],
        mapping=MRC_MAPPING,
    )
    dataset_id = first.dataset_id
    await edits.split_long_documents(db_session, dataset_id=dataset_id, log=None)
    await db_session.commit()
    original = await db_session.scalar(select(Document).where(Document.replaced_at.is_not(None)))

    # 실행: 같은 지문에 새 질문을 더한다(데이터 추가).
    second = await import_csv(
        db_session,
        header=["q", "p", "a"],
        rows=[["환불 규정의 마지막 문장은?", LONG_PASSAGE, "199번째 문장"]],
        mapping=MRC_MAPPING,
        dataset_id=dataset_id,
    )

    # 확인: 긴 원문을 다시 넣지 않고, 판정은 원문(되돌릴 때)과 답이 든 청크에 적는다.
    assert (second.result["documents_added"], second.result["documents_merged"]) == (0, 1)
    assert second.result["judgments_added"] == 1
    query = await db_session.scalar(select(Query).where(Query.text == "환불 규정의 마지막 문장은?"))
    judged = (
        await db_session.execute(
            select(Document.id, Document.text)
            .join(Judgment, Judgment.document_id == Document.id)
            .where(Judgment.query_id == query.id)
            .order_by(Document.id)
        )
    ).all()
    assert len(judged) == 2
    assert judged[0][0] == original.id
    assert "199번째 문장" in judged[1][1]
    long_documents = await db_session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.dataset_id == dataset_id,
            retrieval_service.DOCUMENT_ACTIVE,
            Document.token_count > 512,
        )
    )
    assert long_documents == 0


async def test_import_merges_same_query_across_source_splits(db_session):
    # 준비 · 실행: 같은 질의가 원본의 train · valid · test에 한 번씩 있다.
    finished = await import_csv(
        db_session,
        header=["query", "positive", "split"],
        rows=[
            ["환불 기간", "환불은 3일", "train"],
            ["환불 기간", "환불은 3일", "validation"],
            ["환불 기간", "환불 신청은 마이페이지", "test"],
        ],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
    )

    # 확인: 분할 없이 질의 하나로 합치고, 정답 문서 둘을 모두 가진다.
    assert finished.status == ImportStatus.DONE
    assert await _count(db_session, Query, finished.dataset_id) == 1
    positives = await db_session.scalar(
        select(func.count()).where(Judgment.dataset_id == finished.dataset_id, Judgment.grade >= 1)
    )
    assert positives == 2


class WorkerStopped(Exception):
    """작업 실행기가 도중에 종료된 것을 흉내 내는 예외."""


async def test_import_job_can_run_twice_without_duplicating_rows(db_session, monkeypatch):
    # 준비: 다 넣고 끝내기 직전에 작업 실행기가 종료된 경우를 꾸민다.
    preview = await upload(db_session, "검색.csv", csv_bytes(TRIPLET_HEADER, TRIPLET_ROWS))
    created = await retrieval_service.create_import(
        db_session,
        data=ImportCreate.model_validate(
            {
                "new_dataset_name": "다시 돌리기",
                "source": "file",
                "upload_id": preview.upload_id,
                "mapping": TRIPLET_MAPPING,
            }
        ),
    )

    async def stop_before_finish(*_args: object, **_kwargs: object) -> None:
        raise WorkerStopped

    monkeypatch.setattr(imports_service, "finish_import", stop_before_finish)
    with pytest.raises(WorkerStopped):
        await importing.run_import(db_session, import_id=created.id, job_id=created.job_id)
    monkeypatch.undo()
    job = await jobs.get_job(db_session, job_id=created.job_id)

    # 실행: 작업 실행기가 다시 실행돼 같은 작업을 처음부터 다시 하는 경우
    await run_import_job(job)

    # 확인
    rerun = await imports_service.get_import(db_session, import_id=created.id)
    assert rerun.status == ImportStatus.DONE
    assert await _count(db_session, Query, created.dataset_id) == 3
    assert await _count(db_session, Document, created.dataset_id) == 5
    assert await _count(db_session, Judgment, created.dataset_id) == 7


async def test_import_fails_with_message_when_column_is_missing(db_session):
    # 준비 · 실행
    finished = await import_csv(
        db_session,
        header=["q", "p"],
        rows=[["질의", "문서"]],
        mapping={"shape": "pair", "query": "query", "positive": "p"},
    )

    # 확인
    assert finished.status == ImportStatus.FAILED
    assert finished.error == "'query' 열이 원본에 없습니다. 필드 맞추기를 다시 확인하세요."
    job = await jobs.get_job(db_session, job_id=finished.job_id)
    await db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert await _count(db_session, Query, finished.dataset_id) == 0


async def test_import_documents_keeps_header_group_and_extra_columns(db_session):
    # 준비 · 실행
    await import_csv(
        db_session,
        header=["title", "team", "product", "text", "note"],
        rows=[["설치 안내", "고객지원", "SC-200", "전지 4개를 넣고 덮개를 닫습니다.", "초판"]],
        mapping={
            "shape": "documents",
            "text": "text",
            "title": "title",
            "header_columns": ["team"],
            "group_column": "product",
        },
    )

    # 확인
    document = await db_session.scalar(select(Document))
    assert document.header == "고객지원"
    assert document.group_key == "SC-200"
    assert document.extra == {"note": "초판"}
    assert document.training_text == "설치 안내 › 고객지원\n전지 4개를 넣고 덮개를 닫습니다."


async def test_import_documents_keeps_same_body_with_different_titles_apart(db_session):
    # 준비: 다른 문서에 같은 문장이 있다(운영 풀에서는 서로 다른 문서).
    rows = [
        ["규정 가", "이 규정은 공포한 날부터 시행한다."],
        ["규정 나", "이 규정은 공포한 날부터 시행한다."],
        ["규정 가", "이 규정은 공포한 날부터 시행한다."],
    ]

    # 실행
    finished = await import_csv(
        db_session,
        header=["title", "text"],
        rows=rows,
        mapping={"shape": "documents", "text": "text", "title": "title"},
    )

    # 확인: 제목까지 같은 셋째 줄만 합친다.
    count = await db_session.scalar(select(func.count()).select_from(Document))
    assert count == 2
    assert finished.result["documents_merged"] == 1


async def test_import_marks_broken_text_once(db_session):
    # 준비 · 실행
    await import_csv(
        db_session,
        header=["text"],
        rows=[["깨진 글자 � 가 든 문서입니다. 뒤에도 본문이 이어집니다."]],
        mapping={"shape": "documents", "text": "text"},
    )

    # 확인
    document = await db_session.scalar(select(Document))
    assert document.marks.get("broken") is True
