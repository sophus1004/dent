"""가져오기 테스트: 줄 규칙, 다시 돌리기, 실패 처리, 올린 원본 지우기, 허깅페이스(가짜 응답).

원본 읽기(csv·tsv·xlsx·허깅페이스 미리 보기)는 시스템 테스트(tests/system)가 본다.
"""

import pytest
from sqlalchemy import func, select, update

from dent import worker
from dent.modules.classification import importing
from dent.modules.classification import service as classification_service
from dent.modules.classification.jobs import UNEXPECTED_ERROR_MESSAGE, run_import_job
from dent.modules.classification.models import Label, Record
from dent.modules.classification.schemas import FieldMappingCreate, ImportCreate
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import File, Import, ImportStatus, Job, JobStatus
from tests.modules.classification.helpers import import_and_run
from tests.system.helpers import (
    HF_REPO,
    huggingface_transport,
    upload,
    upload_state,
    xlsx_bytes,
)


class WorkerStopped(Exception):
    """작업 실행기가 도중에 종료된 것을 흉내 내는 예외."""


def _file_import(upload_id: str, **overrides: object) -> ImportCreate:
    """새 데이터셋으로 파일을 가져오는 입력."""
    values: dict[str, object] = {
        "new_dataset_name": "가져온 데이터",
        "source": "file",
        "upload_id": upload_id,
        "mapping": {"text": "text", "label": "label"},
    }
    values.update(overrides)
    return ImportCreate.model_validate(values)


# ---------- 파일 가져오기 ----------


async def test_import_file_skips_bad_lines_with_line_numbers(db_session):
    # 준비
    too_long = "가" * 10_001
    content = (
        "text,label,split,source\n"
        "안녕하세요,인사,train,a\n"
        ",인사,train,b\n"
        '"여러 줄로\n된 문장",인사,valid,c\n'
        "라벨 없는 문장,,train,d\n"
        f"{too_long},인사,train,e\n"
        "환불해 주세요,환불,dev,f\n"
    ).encode()
    preview = await upload(db_session, "상담.csv", content)

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    assert finished.status == ImportStatus.DONE
    assert finished.rows_total == 6
    assert finished.rows_added == 3
    assert finished.rows_skipped == 3
    assert finished.skipped_lines == [
        {"line": 3, "reason": "빈 문장"},
        {"line": 6, "reason": "라벨 없음"},
        {"line": 7, "reason": "너무 긴 문장"},
    ]
    assert finished.result["new_labels"] == ["인사", "환불"]
    records = (await db_session.scalars(select(Record).order_by(Record.id))).all()
    assert [record.text for record in records] == [
        "안녕하세요",
        "여러 줄로\n된 문장",
        "환불해 주세요",
    ]
    # 원본의 분할 열도 남는 열이라 extra에 담긴다(분할로 쓰지 않는다)
    assert records[0].extra == {"split": "train", "source": "a"}
    assert records[0].import_id == finished.id


async def test_import_file_merges_source_splits_into_one(db_session):
    # 준비
    content = (
        "text,label,split\n"
        "배송 언제 와요,배송,train\n"
        "환불해 주세요,환불,validation\n"
        "배송 언제 와요,배송,test\n"
    ).encode()
    preview = await upload(db_session, "분할.csv", content)

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    # 분할이 달라도 모두 한 덩어리로 들어오고, 같은 문장은 중복으로 남는다(진단이 찾는다).
    assert finished.rows_added == 3
    texts = (await db_session.scalars(select(Record.text).order_by(Record.id))).all()
    assert texts == ["배송 언제 와요", "환불해 주세요", "배송 언제 와요"]


async def test_import_xlsx_reports_sheet_row_numbers(db_session):
    # 준비
    preview = await upload(db_session, "두 시트.xlsx", xlsx_bytes())

    # 실행
    finished = await import_and_run(
        db_session,
        _file_import(preview.upload_id, sheet="둘째", mapping={"text": "문장", "label": "의도"}),
    )

    # 확인
    assert finished.rows_added == 2
    assert finished.skipped_lines == [{"line": 4, "reason": "빈 문장"}]
    records = (await db_session.scalars(select(Record).order_by(Record.id))).all()
    assert records[1].extra == {"번호": 3}


async def test_import_job_can_run_twice_without_duplicating_records(db_session, monkeypatch):
    # 준비: 문장을 다 넣고 끝내기 직전에 작업 실행기가 종료된 경우를 꾸민다.
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n반가워,인사\n".encode())
    created = await classification_service.create_import(
        db_session, data=_file_import(preview.upload_id)
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
    count = await db_session.scalar(select(func.count()).select_from(Record))
    rerun = await imports_service.get_import(db_session, import_id=created.id)
    assert rerun.status == ImportStatus.DONE
    assert count == 2
    assert rerun.rows_added == 2
    assert rerun.result["new_labels"] == ["인사"]
    # 끊긴 동안 남겨 둔 원본을 다시 읽었고, 끝났으니 이제 지웠다.
    assert await upload_state(db_session, preview.upload_id) == (False, True)


async def test_import_adds_to_existing_dataset_and_reuses_labels(db_session):
    # 준비
    first = await upload(db_session, "하나.csv", "text,label\n안녕,인사\n".encode())
    created = await import_and_run(db_session, _file_import(first.upload_id))
    second = await upload(db_session, "둘.csv", "text,label\n또 안녕,인사\n환불요,환불\n".encode())

    # 실행
    finished = await import_and_run(
        db_session,
        _file_import(second.upload_id, new_dataset_name=None, dataset_id=created.dataset_id),
    )

    # 확인
    labels = (await db_session.scalars(select(Label.name).order_by(Label.name))).all()
    assert finished.result["new_labels"] == ["환불"]
    assert labels == ["인사", "환불"]


async def test_import_fails_with_korean_error_when_mapping_column_is_missing(db_session):
    # 준비
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())

    # 실행
    finished = await import_and_run(
        db_session,
        _file_import(preview.upload_id, mapping={"text": "없는열", "label": "label"}),
    )

    # 확인
    job = await jobs.get_job(db_session, job_id=finished.job_id)
    await db_session.refresh(job)
    assert finished.status == ImportStatus.FAILED
    assert finished.error is not None and "'없는열' 열이 원본에 없습니다" in finished.error
    assert job.status == JobStatus.FAILED
    assert job.error == finished.error


async def test_import_fails_when_label_column_has_too_many_values(db_session, monkeypatch):
    # 준비
    monkeypatch.setattr(importing, "MAX_LABELS_PER_DATASET", 2)
    preview = await upload(db_session, "번호.csv", "text,label\n하나,1\n둘,2\n셋,3\n".encode())

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    labels = (await db_session.scalars(select(Label.name))).all()
    assert finished.status == ImportStatus.FAILED
    assert finished.error is not None and "라벨이 2개를 넘습니다" in finished.error
    assert labels == []


async def test_import_writes_same_message_to_import_and_job_on_unexpected_error(
    db_session, monkeypatch
):
    # 준비
    async def broken_run_import(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("뜻밖의 오류")

    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())
    monkeypatch.setattr(importing, "run_import", broken_run_import)

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    job = await jobs.get_job(db_session, job_id=finished.job_id)
    await db_session.refresh(job)
    assert finished.status == ImportStatus.FAILED
    assert finished.error == UNEXPECTED_ERROR_MESSAGE
    assert job.error == UNEXPECTED_ERROR_MESSAGE


async def test_recovered_import_fails_and_removes_half_inserted_records(db_session):
    # 준비: 문장을 넣은 가져오기를, 작업 실행기가 너무 여러 번 끊긴 '실행 중' 상태로 꾸민다.
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n반가워,인사\n".encode())
    finished = await import_and_run(db_session, _file_import(preview.upload_id))
    await db_session.execute(
        update(Job)
        .where(Job.id == finished.job_id)
        .values(status=JobStatus.RUNNING, attempts=jobs.MAX_ATTEMPTS)
    )
    await db_session.execute(
        update(Import).where(Import.id == finished.id).values(status=ImportStatus.RUNNING)
    )
    await db_session.commit()

    # 실행: 작업 실행기가 실행될 때 하는 일
    recovered = await jobs.recover_interrupted_jobs(db_session)
    for job in recovered.failed:
        await worker.FAILURE_HANDLERS[(job.module, job.kind)](job)

    # 확인
    stopped = await imports_service.get_import(db_session, import_id=finished.id)
    count = await db_session.scalar(select(func.count()).select_from(Record))
    assert stopped.status == ImportStatus.FAILED
    assert stopped.error is not None and "끊겨" in stopped.error
    assert count == 0


async def test_import_marks_progress_and_result_on_job(db_session):
    # 준비
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n,인사\n".encode())

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    job = await jobs.get_job(db_session, job_id=finished.job_id)
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE
    assert job.progress_done == 2
    assert job.progress_total == 2
    assert job.result == {"import_id": finished.id, "rows_added": 1, "rows_skipped": 1}


# ---------- 올린 원본 지우기 ----------


async def test_import_file_removes_upload_but_keeps_record_when_done(db_session):
    # 준비
    content = "text,label\n안녕,인사\n".encode()
    preview = await upload(db_session, "인사.csv", content)

    # 실행
    finished = await import_and_run(db_session, _file_import(preview.upload_id))

    # 확인
    file = await db_session.get(File, preview.upload_id)
    assert finished.status == ImportStatus.DONE
    assert await upload_state(db_session, preview.upload_id) == (False, True)
    assert file is not None
    assert (file.original_name, file.size_bytes) == ("인사.csv", len(content))


async def test_import_file_removes_upload_when_mapping_column_is_missing(db_session):
    # 준비
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())

    # 실행
    finished = await import_and_run(
        db_session,
        _file_import(preview.upload_id, mapping={"text": "없는열", "label": "label"}),
    )

    # 확인
    # 같은 파일로 다시 시도할 수 없으므로 화면은 [다시 올리기]를 보인다.
    assert finished.status == ImportStatus.FAILED
    assert await upload_state(db_session, preview.upload_id) == (False, True)


async def test_import_file_keeps_upload_while_interrupted_job_is_requeued(db_session):
    # 준비: 작업 실행기가 가져오기를 꺼낸 뒤 종료됐다.
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())
    created = await classification_service.create_import(
        db_session, data=_file_import(preview.upload_id)
    )
    await jobs.claim_next_job(db_session)

    # 실행: 작업 실행기가 다시 실행될 때 하는 일
    recovered = await jobs.recover_interrupted_jobs(db_session)

    # 확인
    # 다시 대기로 돌아간 작업이 원본을 다시 읽어야 하므로 지우지 않는다.
    job = await jobs.get_job(db_session, job_id=created.job_id)
    await db_session.refresh(job)
    assert (recovered.requeued, job.status) == (1, JobStatus.QUEUED)
    assert await upload_state(db_session, preview.upload_id) == (True, False)


async def test_import_file_removes_upload_when_interrupted_too_many_times(db_session):
    # 준비: 작업 실행기가 가져오기를 꺼낸 뒤 종료되기를 너무 여러 번 했다.
    preview = await upload(db_session, "인사.csv", "text,label\n안녕,인사\n".encode())
    created = await classification_service.create_import(
        db_session, data=_file_import(preview.upload_id)
    )
    await jobs.claim_next_job(db_session)
    await db_session.execute(
        update(Job).where(Job.id == created.job_id).values(attempts=jobs.MAX_ATTEMPTS)
    )
    await db_session.commit()

    # 실행: 작업 실행기가 실행될 때 하는 일
    recovered = await jobs.recover_interrupted_jobs(db_session)
    for job in recovered.failed:
        await worker.FAILURE_HANDLERS[(job.module, job.kind)](job)

    # 확인
    stopped = await imports_service.get_import(db_session, import_id=created.id)
    assert stopped.status == ImportStatus.FAILED
    assert await upload_state(db_session, preview.upload_id) == (False, True)


# ---------- 허깅페이스 ----------


async def test_import_huggingface_reads_parquet_with_label_names(db_session):
    # 준비
    data = ImportCreate(
        new_dataset_name="리뷰 감성",
        source="huggingface",
        repo=HF_REPO,
        config="default",
        mapping=FieldMappingCreate(text="text", label="label"),
    )

    # 실행
    finished = await import_and_run(db_session, data, transport=huggingface_transport())

    # 확인
    assert finished.status == ImportStatus.DONE, finished.error
    assert finished.source_name == f"{HF_REPO} · default"
    assert finished.rows_total == 4
    assert finished.rows_added == 3
    assert finished.skipped_lines == [{"line": 3, "reason": "라벨 없음"}]
    assert finished.result["new_labels"] == ["긍정", "부정"]
    # train · validation을 모두 한 데이터셋으로 합친다.
    query = select(Record.text, Label.name).join(Label).order_by(Record.id)
    rows = (await db_session.execute(query)).all()
    assert [tuple(row) for row in rows] == [
        ("좋아요", "긍정"),
        ("별로예요", "부정"),
        ("최고예요", "긍정"),
    ]


async def test_import_huggingface_reads_only_chosen_splits(db_session):
    # 준비
    data = ImportCreate(
        new_dataset_name="검증만",
        source="huggingface",
        repo=HF_REPO,
        config="default",
        splits=["validation"],
        mapping=FieldMappingCreate(text="text", label="label"),
    )

    # 실행
    finished = await import_and_run(db_session, data, transport=huggingface_transport())

    # 확인
    assert finished.rows_added == 1
    texts = (await db_session.scalars(select(Record.text))).all()
    assert texts == ["최고예요"]


async def test_import_huggingface_fails_when_split_is_only_partly_converted(db_session):
    # 준비
    data = ImportCreate(
        new_dataset_name="큰 데이터",
        source="huggingface",
        repo=HF_REPO,
        config="default",
        mapping=FieldMappingCreate(text="text", label="label"),
    )

    # 실행
    finished = await import_and_run(
        db_session, data, transport=huggingface_transport(partial_train=True)
    )

    # 확인
    # 앞부분만 받아 '다 가져왔다'고 하지 않는다.
    assert finished.status == ImportStatus.FAILED
    assert finished.error is not None and "'train' 분할을 앞부분만" in finished.error
