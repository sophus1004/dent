"""가져오기 기록 테스트: 기록과 작업 만들기, 묶음 결과 더하기, 실패로 끝내기."""

import pytest

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import Import, ImportSource, ImportStatus, JobStatus
from tests.system.helpers import upload


async def _queued_import(db_session) -> Import:
    """새 데이터셋에 대기 중인 가져오기 하나를 만든다."""
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="상담"
    )
    import_row = await imports_service.create_import(
        db_session,
        module="classification",
        dataset_id=dataset.id,
        source=ImportSource.HUGGINGFACE,
        source_name="klue/klue",
        options={"repo": "klue/klue"},
    )
    await db_session.commit()
    return await imports_service.get_import(db_session, import_id=import_row.id)


async def test_create_import_queues_job_of_module_with_import_kind(db_session):
    # 실행
    import_row = await _queued_import(db_session)

    # 확인
    assert import_row.job_id is not None
    job = await jobs.get_job(db_session, job_id=import_row.job_id)
    assert (job.module, job.kind, job.params) == (
        "classification",
        "import",
        {"import_id": import_row.id},
    )
    assert (import_row.status, import_row.result, import_row.skipped_lines) == ("queued", {}, [])


async def test_add_batch_keeps_only_first_skipped_lines(db_session, monkeypatch):
    # 준비
    monkeypatch.setattr(imports_service, "SKIPPED_LINES_KEPT", 3)
    import_row = await _queued_import(db_session)
    imports_service.start_import(import_row)

    # 실행
    imports_service.add_batch(
        import_row, added=5, skipped=[{"line": 2, "reason": "빈 문장"}, {"line": 4, "reason": "x"}]
    )
    imports_service.add_batch(
        import_row, added=1, skipped=[{"line": 9, "reason": "x"}, {"line": 10, "reason": "x"}]
    )
    await db_session.commit()

    # 확인
    saved = await imports_service.get_import(db_session, import_id=import_row.id)
    assert (saved.rows_added, saved.rows_skipped) == (6, 4)
    assert [line["line"] for line in saved.skipped_lines] == [2, 4, 9]


async def test_finish_import_marks_import_and_job_done(db_session):
    # 준비
    import_row = await _queued_import(db_session)
    imports_service.start_import(import_row)
    imports_service.add_batch(import_row, added=2, skipped=[])

    # 실행
    await imports_service.finish_import(db_session, import_row, job_id=import_row.job_id)
    await db_session.commit()

    # 확인
    job = await jobs.get_job(db_session, job_id=import_row.job_id)
    await db_session.refresh(job)
    assert import_row.status == ImportStatus.DONE
    assert import_row.finished_at is not None
    assert job.status == JobStatus.DONE
    assert job.result == {"import_id": import_row.id, "rows_added": 2, "rows_skipped": 0}


async def test_fail_import_writes_same_message_to_import_and_job(db_session):
    # 준비
    import_row = await _queued_import(db_session)

    # 실행
    await imports_service.fail_import(
        db_session, import_id=import_row.id, job_id=import_row.job_id, message="원본이 없습니다."
    )

    # 확인
    failed = await imports_service.get_import(db_session, import_id=import_row.id)
    job = await jobs.get_job(db_session, job_id=import_row.job_id)
    await db_session.refresh(job)
    assert (failed.status, failed.error) == ("failed", "원본이 없습니다.")
    assert (job.status, job.error) == ("failed", "원본이 없습니다.")


async def test_list_imports_fails_when_dataset_is_missing(db_session):
    # 실행
    with pytest.raises(NotFoundError):
        await imports_service.list_imports(db_session, dataset_id=999)


async def test_create_import_returns_conflict_when_same_upload_is_being_imported(db_session):
    # 준비: 올린 파일 하나를 데이터셋 A에 가져오는 중
    stored = await upload(db_session, "상담.csv", "text,label\n안녕,인사\n".encode())
    first = await datasets_service.create_dataset(db_session, module="classification", name="A")
    second = await datasets_service.create_dataset(db_session, module="classification", name="B")
    options = {"upload_id": stored.upload_id, "sheet": None}
    await imports_service.create_import(
        db_session,
        module="classification",
        dataset_id=first.id,
        source=ImportSource.FILE,
        source_name="상담.csv",
        options=options,
    )
    await db_session.commit()

    # 실행 · 확인: 같은 파일을 B에도 가져오면 먼저 끝난 쪽이 파일을 지우므로 막는다
    with pytest.raises(ConflictError, match="이미 가져오는 중"):
        await imports_service.create_import(
            db_session,
            module="classification",
            dataset_id=second.id,
            source=ImportSource.FILE,
            source_name="상담.csv",
            options=options,
        )
