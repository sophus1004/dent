"""작업 대기열 테스트: 꺼내기(데이터셋마다 차례 · 데이터셋끼리 함께), 끊긴 작업 되살리기(죽은 실행기의 것만),
번호 자리 잡기, 겹쳐 넣지 않기, 정리 작업."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system import uploads as uploads_service
from dent.system.db import get_engine, get_sessionmaker
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


async def test_worker_lock_allows_only_one_holder_per_slot(db_session):
    # 준비
    engine = get_engine()
    async with engine.connect() as first, engine.connect() as second:
        # 실행
        first_acquired = await jobs.try_acquire_worker_lock(first, slot=1)
        second_acquired = await jobs.try_acquire_worker_lock(second, slot=1)
        other_slot_acquired = await jobs.try_acquire_worker_lock(second, slot=2)
        running_while_held = await jobs.running_worker_slots(db_session)
        await jobs.release_worker_lock(first, slot=1)
        await jobs.release_worker_lock(second, slot=2)
        running_after_release = await jobs.is_worker_running(db_session)

    # 확인
    assert first_acquired
    assert not second_acquired
    assert other_slot_acquired
    assert running_while_held == [1, 2]
    assert not running_after_release


async def test_acquire_free_worker_slot_takes_smallest_free_slot(db_session):
    # 준비
    engine = get_engine()
    async with engine.connect() as first, engine.connect() as second:
        await jobs.try_acquire_worker_lock(first, slot=1)

        # 실행
        slot = await jobs.acquire_free_worker_slot(second)
        await jobs.release_worker_lock(second, slot=2)
        await jobs.release_worker_lock(first, slot=1)

    # 확인
    assert slot == 2


async def test_maintenance_lock_allows_only_one_worker(db_session):
    # 준비
    engine = get_engine()
    async with engine.connect() as first, engine.connect() as second:
        # 실행
        first_acquired = await jobs.try_acquire_maintenance_lock(first)
        second_acquired = await jobs.try_acquire_maintenance_lock(second)
        await jobs.release_maintenance_lock(first)
        after_release = await jobs.try_acquire_maintenance_lock(second)
        await jobs.release_maintenance_lock(second)
        held = await jobs.running_worker_slots(db_session)

    # 확인: 정리 잠금은 실행기 번호로 세지 않는다
    assert first_acquired
    assert not second_acquired
    assert after_release
    assert held == []


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


# ---------- 여러 실행기: 데이터셋마다 차례 · 데이터셋끼리 함께 ----------


async def _datasets(db_session, *names: str) -> list[int]:
    made = [
        await datasets_service.create_dataset(db_session, module="classification", name=name)
        for name in names
    ]
    await db_session.commit()
    return [dataset.id for dataset in made]


async def _enqueue(db_session, kind: str, dataset_id: int | None) -> Job:
    job = await jobs.enqueue_job(
        db_session, module="classification", kind=kind, params={}, dataset_id=dataset_id
    )
    await db_session.commit()
    return job


async def _claim(worker: int) -> Job | None:
    """실행기 하나처럼 자기 세션으로 꺼낸다."""
    async with get_sessionmaker()() as session:
        return await jobs.claim_next_job(session, worker=worker)


async def test_claim_next_job_skips_dataset_with_running_job(db_session):
    # 준비: A에 두 작업, B에 한 작업
    a, b = await _datasets(db_session, "A", "B")
    await _enqueue(db_session, "a1", a)
    await _enqueue(db_session, "a2", a)
    await _enqueue(db_session, "b1", b)

    # 실행: 실행기마다 자기 세션으로 꺼낸다
    first = await _claim(1)
    second = await _claim(2)
    third = await _claim(3)

    # 확인: A의 둘째 작업은 첫 작업이 끝날 때까지 기다리고, 그동안 B가 돈다
    assert first is not None and (first.kind, first.worker) == ("a1", 1)
    assert second is not None and (second.kind, second.worker) == ("b1", 2)
    assert third is None


async def test_claim_next_job_takes_next_job_of_dataset_after_first_finishes(db_session):
    # 준비
    (a,) = await _datasets(db_session, "A")
    await _enqueue(db_session, "a1", a)
    await _enqueue(db_session, "a2", a)
    first = await jobs.claim_next_job(db_session, worker=1)
    assert first is not None

    # 실행
    await jobs.finish_job(db_session, job_id=first.id, result={})
    await db_session.commit()
    second = await jobs.claim_next_job(db_session, worker=2)

    # 확인
    assert second is not None and second.kind == "a2"


async def test_claim_next_job_keeps_order_when_workers_claim_at_once(db_session):
    # 준비: 데이터셋 셋에 두 작업씩
    ids = await _datasets(db_session, "A", "B", "C")
    for dataset_id in ids:
        await _enqueue(db_session, "first", dataset_id)
        await _enqueue(db_session, "second", dataset_id)
    # 실행: 실행기 넷이 동시에 꺼낸다
    claimed = await asyncio.gather(*(_claim(worker) for worker in range(1, 5)))

    # 확인: 데이터셋마다 첫 작업 하나씩만 돈다
    taken = [job for job in claimed if job is not None]
    assert sorted(job.dataset_id for job in taken) == sorted(ids)
    assert {job.kind for job in taken} == {"first"}


async def test_running_job_is_unique_per_dataset(db_session):
    # 준비
    (a,) = await _datasets(db_session, "A")
    db_session.add(Job(module="classification", kind="x", dataset_id=a, status=JobStatus.RUNNING))
    await db_session.commit()

    # 실행 · 확인: 같은 데이터셋에 도는 작업을 하나 더 두면 DB가 막는다
    db_session.add(Job(module="classification", kind="y", dataset_id=a, status=JobStatus.RUNNING))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_recover_interrupted_jobs_leaves_jobs_of_live_workers(db_session):
    # 준비: 실행기 1은 살아 있고, 실행기 2는 죽었다(잠금 없음)
    a, b = await _datasets(db_session, "A", "B")
    alive = Job(module="classification", kind="x", dataset_id=a, status=JobStatus.RUNNING, worker=1)
    dead = Job(module="classification", kind="y", dataset_id=b, status=JobStatus.RUNNING, worker=2)
    db_session.add_all([alive, dead])
    await db_session.commit()
    async with get_engine().connect() as lock_conn:
        await jobs.try_acquire_worker_lock(lock_conn, slot=1)

        # 실행
        result = await jobs.recover_interrupted_jobs(db_session)
        await jobs.release_worker_lock(lock_conn, slot=1)

    # 확인
    await db_session.refresh(alive)
    await db_session.refresh(dead)
    assert result.requeued == 1
    assert (alive.status, alive.worker) == (JobStatus.RUNNING, 1)
    assert (dead.status, dead.worker, dead.attempts) == (JobStatus.QUEUED, None, 1)


async def test_recover_interrupted_jobs_takes_jobs_left_by_same_slot_on_restart(db_session):
    # 준비: 실행기 1이 죽고 다시 떠서 같은 번호를 잡았다
    (a,) = await _datasets(db_session, "A")
    left = Job(module="classification", kind="x", dataset_id=a, status=JobStatus.RUNNING, worker=1)
    db_session.add(left)
    await db_session.commit()
    async with get_engine().connect() as lock_conn:
        await jobs.try_acquire_worker_lock(lock_conn, slot=1)

        # 실행
        result = await jobs.recover_interrupted_jobs(db_session, restarted_slot=1)
        await jobs.release_worker_lock(lock_conn, slot=1)

    # 확인
    await db_session.refresh(left)
    assert result.requeued == 1
    assert left.status == JobStatus.QUEUED


async def test_enqueue_job_with_dedupe_key_keeps_one_queued_job(db_session):
    # 준비
    (a,) = await _datasets(db_session, "A")

    # 실행
    first = await jobs.enqueue_job(
        db_session,
        module="generation",
        kind="contamination",
        params={},
        dataset_id=a,
        dedupe_key="generation:contamination:1",
    )
    again = await jobs.enqueue_job(
        db_session,
        module="generation",
        kind="contamination",
        params={},
        dataset_id=a,
        dedupe_key="generation:contamination:1",
    )
    await db_session.commit()

    # 확인
    assert again.id == first.id
    count = await db_session.scalar(select(func.count()).select_from(Job))
    assert count == 1


async def test_enqueue_job_with_dedupe_key_adds_new_job_once_old_one_runs(db_session):
    # 준비
    (a,) = await _datasets(db_session, "A")
    first = await jobs.enqueue_job(
        db_session,
        module="generation",
        kind="length",
        params={},
        dataset_id=a,
        dedupe_key="generation:length:1",
    )
    await db_session.commit()
    await jobs.claim_next_job(db_session, worker=1)

    # 실행: 도는 동안 설정이 또 바뀌면 다시 재야 하므로 새로 넣는다
    again = await jobs.enqueue_job(
        db_session,
        module="generation",
        kind="length",
        params={},
        dataset_id=a,
        dedupe_key="generation:length:1",
    )
    await db_session.commit()

    # 확인
    assert again.id != first.id
    assert again.status == JobStatus.QUEUED


async def test_waiting_reasons_tell_dataset_wait_from_worker_wait(db_session):
    # 준비: A는 도는 작업 뒤에 하나, B는 대기 하나뿐
    a, b = await _datasets(db_session, "A", "B")
    await _enqueue(db_session, "a1", a)
    a2 = await _enqueue(db_session, "a2", a)
    b1 = await _enqueue(db_session, "b1", b)
    await jobs.claim_next_job(db_session, worker=1)
    await db_session.refresh(a2)
    await db_session.refresh(b1)

    # 실행
    reasons = await jobs.waiting_reasons(db_session, jobs=[a2, b1])

    # 확인
    assert reasons == {a2.id: jobs.WAITING_DATASET, b1.id: jobs.WAITING_WORKER}


async def test_cancel_dataset_jobs_stops_queued_and_marks_running(db_session):
    # 준비
    (a,) = await _datasets(db_session, "A")
    await _enqueue(db_session, "a1", a)
    queued = await _enqueue(db_session, "a2", a)
    running = await jobs.claim_next_job(db_session, worker=1)
    assert running is not None

    # 실행
    await jobs.cancel_dataset_jobs(db_session, dataset_id=a)
    await db_session.commit()

    # 확인
    await db_session.refresh(queued)
    await db_session.refresh(running)
    assert queued.status == JobStatus.CANCELED
    assert running.status == JobStatus.RUNNING and running.cancel_requested


async def test_list_jobs_with_finished_after_is_in_finish_order(db_session):
    # 준비: 늦게 넣었지만 먼저 끝난 작업
    now = datetime.now(UTC)
    early = Job(
        module="classification",
        kind="early",
        status=JobStatus.DONE,
        finished_at=now - timedelta(seconds=5),
    )
    late = Job(module="classification", kind="late", status=JobStatus.DONE, finished_at=now)
    db_session.add_all([late, early])
    await db_session.commit()

    # 실행
    page = await jobs.list_jobs(db_session, finished_after=now - timedelta(minutes=1))

    # 확인
    assert [job.kind for job in page.items] == ["early", "late"]
