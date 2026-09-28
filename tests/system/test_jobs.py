"""작업 대기열 테스트: 꺼내기, 끊긴 작업 되살리기, 자리 잡기, 정리 작업."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system import uploads as uploads_service
from dent.system.db import get_engine
from dent.system.models import File, ImportSource, Job, JobStatus
from tests.system.helpers import upload, upload_state


async def test_claim_next_job_takes_oldest_queued_job(db_session):
    # 준비
    db_session.add_all(
        [Job(module="classification", kind="first"), Job(module="classification", kind="second")]
    )
    await db_session.commit()

    # 실행
    claimed = await jobs.claim_next_job(db_session)

    # 확인
    assert claimed is not None
    assert claimed.kind == "first"
    assert claimed.status == JobStatus.RUNNING


async def test_recover_interrupted_jobs_requeues_running_job(db_session):
    # 준비
    job = Job(module="classification", kind="analyze", status=JobStatus.RUNNING)
    db_session.add(job)
    await db_session.commit()

    # 실행
    result = await jobs.recover_interrupted_jobs(db_session)

    # 확인
    await db_session.refresh(job)
    assert result.requeued == 1
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1


async def test_recover_interrupted_jobs_fails_job_after_too_many_attempts(db_session):
    # 준비
    job = Job(
        module="classification",
        kind="analyze",
        status=JobStatus.RUNNING,
        attempts=jobs.MAX_ATTEMPTS,
    )
    db_session.add(job)
    await db_session.commit()

    # 실행
    result = await jobs.recover_interrupted_jobs(db_session)

    # 확인
    await db_session.refresh(job)
    assert [failed.id for failed in result.failed] == [job.id]
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "끊겨" in job.error


async def test_worker_lock_allows_only_one_holder(db_session):
    # 준비
    engine = get_engine()
    async with engine.connect() as first, engine.connect() as second:
        # 실행
        first_acquired = await jobs.try_acquire_worker_lock(first)
        second_acquired = await jobs.try_acquire_worker_lock(second)
        running_while_held = await jobs.is_worker_running(db_session)
        await jobs.release_worker_lock(first)
        running_after_release = await jobs.is_worker_running(db_session)

    # 확인
    assert first_acquired
    assert not second_acquired
    assert running_while_held
    assert not running_after_release


async def test_cleanup_is_due_until_it_runs_once(db_session):
    # 실행
    due_before = await jobs.is_cleanup_due(db_session)
    await jobs.run_cleanup(db_session)
    due_after = await jobs.is_cleanup_due(db_session)

    # 확인
    assert due_before
    assert not due_after


async def test_run_cleanup_discards_old_uploads_unless_recent_or_used_by_queued_import(db_session):
    # 준비: 오래됐고 쓰지 않는 파일, 막 올린 파일, 오래됐지만 대기 중인 가져오기가 쓰는 파일
    content = "text,label\n안녕,인사\n".encode()
    old = await upload(db_session, "오래됨.csv", content)
    recent = await upload(db_session, "막 올림.csv", content)
    in_use = await upload(db_session, "가져올 것.csv", content)
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="상담"
    )
    await imports_service.create_import(
        db_session,
        module="classification",
        dataset_id=dataset.id,
        source=ImportSource.FILE,
        source_name="가져올 것.csv",
        options={"upload_id": in_use.upload_id, "sheet": None},
    )
    long_ago = datetime.now(UTC) - uploads_service.UNUSED_UPLOAD_RETENTION - timedelta(hours=1)
    await db_session.execute(
        update(File)
        .where(File.id.in_([old.upload_id, in_use.upload_id]))
        .values(created_at=long_ago)
    )
    await db_session.commit()

    # 실행
    cleaned = await jobs.run_cleanup(db_session)

    # 확인
    assert cleaned.deleted_uploads == 1
    assert await upload_state(db_session, old.upload_id) == (False, True)
    assert await upload_state(db_session, recent.upload_id) == (True, False)
    assert await upload_state(db_session, in_use.upload_id) == (True, False)


# ---------- 단계 · 멈추기 ----------


async def test_start_phase_writes_phase_and_resets_progress(db_session):
    # 준비
    job = Job(module="classification", kind="map", status=JobStatus.RUNNING, progress_done=5)
    db_session.add(job)
    await db_session.commit()

    # 실행
    await jobs.start_phase(db_session, job_id=job.id, phase="embedding", total=10)
    await db_session.commit()

    # 확인
    await db_session.refresh(job)
    assert job.result is not None
    assert job.result["phase"] == "embedding"
    assert "phase_started_at" in job.result
    assert (job.progress_done, job.progress_total) == (0, 10)


async def test_request_cancel_stops_queued_job_at_once(db_session):
    # 준비
    job = Job(module="classification", kind="map")
    db_session.add(job)
    await db_session.commit()

    # 실행
    stopped = await jobs.request_cancel(db_session, job_id=job.id)
    await db_session.commit()

    # 확인
    await db_session.refresh(job)
    assert stopped
    assert job.status == JobStatus.CANCELED
    assert job.finished_at is not None


async def test_request_cancel_only_marks_running_job(db_session):
    # 준비
    job = Job(module="classification", kind="map", status=JobStatus.RUNNING)
    db_session.add(job)
    await db_session.commit()

    # 실행
    stopped = await jobs.request_cancel(db_session, job_id=job.id)
    await db_session.commit()

    # 확인
    await db_session.refresh(job)
    assert not stopped
    assert job.status == JobStatus.RUNNING
    assert await jobs.is_cancel_requested(db_session, job_id=job.id)


async def test_start_phase_appends_phase_events_without_losing_earlier_ones(db_session):
    # 준비
    job = Job(module="classification", kind="map", status=JobStatus.RUNNING)
    db_session.add(job)
    await db_session.commit()
    await jobs.add_event(db_session, job_id=job.id, event_type=jobs.EVENT_CONNECTION, model="m")

    # 실행
    await jobs.start_phase(db_session, job_id=job.id, phase="embedding", total=10)
    await jobs.start_phase(db_session, job_id=job.id, phase="projecting", total=None)
    await db_session.commit()

    # 확인
    await db_session.refresh(job)
    assert [(event["type"], event.get("phase")) for event in job.events] == [
        ("connection", None),
        ("phase", "embedding"),
        ("phase", "projecting"),
    ]
    assert all("at" in event for event in job.events)
    assert job.result is not None and job.result["phase"] == "projecting"


async def test_claim_next_job_records_start_event_with_attempt(db_session):
    # 준비: 한 번 끊겼다가 다시 대기로 돌아온 작업
    job = Job(module="classification", kind="map", attempts=1)
    db_session.add(job)
    await db_session.commit()

    # 실행
    claimed = await jobs.claim_next_job(db_session)

    # 확인
    assert claimed is not None
    await db_session.refresh(job)
    assert [(event["type"], event["attempt"]) for event in job.events] == [("start", 2)]


async def test_enqueue_job_keeps_dataset_name_after_dataset_is_deleted(db_session):
    # 준비
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="뉴스"
    )
    job = await jobs.enqueue_job(
        db_session, module="classification", kind="map", params={}, dataset_id=dataset.id
    )
    await db_session.commit()
    assert (job.dataset_id, job.dataset_name) == (dataset.id, "뉴스")

    # 실행
    await datasets_service.delete_dataset(
        db_session, dataset_id=dataset.id, module="classification"
    )
    await db_session.commit()

    # 확인
    await db_session.refresh(job)
    assert (job.dataset_id, job.dataset_name) == (None, "뉴스")


async def test_record_event_writes_with_its_own_session(db_session):
    # 준비
    job = Job(module="classification", kind="map", status=JobStatus.RUNNING)
    db_session.add(job)
    await db_session.commit()

    # 실행
    await jobs.record_event(
        job_id=job.id, event_type=jobs.EVENT_RETRY, attempt=1, reason="시간 초과"
    )

    # 확인
    await db_session.refresh(job)
    assert [(event["type"], event["attempt"], event["reason"]) for event in job.events] == [
        ("retry", 1, "시간 초과")
    ]
