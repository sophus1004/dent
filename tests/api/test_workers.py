"""작업 실행기 API 테스트: 정한 수(기본 · 저장 · 범위 밖 422), 번호마다 상태(잠금 · 도는 작업 · 관리 프로세스가 적은 상태)."""

import os

import psutil
from fastapi import status

from dent.system import datasets as datasets_service
from dent.system import jobs
from dent.system import workers as workers_service
from dent.system.config import get_settings
from dent.system.db import get_engine
from dent.system.workers import SlotState

API = "/api/v1/workers"


async def test_get_workers_returns_default_count_when_nothing_is_saved(client, db_session):
    # 실행
    response = await client.get(API)

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["count"], body["min_count"], body["max_count"]) == (4, 1, 8)
    assert body["managed"] is False
    assert [slot["state"] for slot in body["slots"]] == ["stopped"] * 4


async def test_update_workers_saves_count(client, db_session):
    # 실행
    response = await client.put(API, json={"count": 2})
    again = await client.get(API)

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["count"] == 2
    assert again.json()["count"] == 2
    assert await workers_service.get_worker_count(db_session) == 2


async def test_update_workers_returns_422_when_count_is_out_of_range(client, db_session):
    # 실행
    too_few = await client.put(API, json={"count": 0})
    too_many = await client.put(API, json={"count": 9})

    # 확인
    assert too_few.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert too_many.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_get_workers_shows_running_slot_with_its_job(client, db_session):
    # 준비: 실행기 1이 자리를 잡고 데이터셋의 작업을 돈다
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="뉴스"
    )
    await jobs.enqueue_job(
        db_session, module="classification", kind="map", params={}, dataset_id=dataset.id
    )
    await db_session.commit()
    await jobs.claim_next_job(db_session, worker=1)
    async with get_engine().connect() as lock_conn:
        await jobs.try_acquire_worker_lock(lock_conn, slot=1)

        # 실행
        response = await client.get(API)
        await jobs.release_worker_lock(lock_conn, slot=1)

    # 확인
    body = response.json()
    first = body["slots"][0]
    assert (body["running"], body["busy"], body["queued"]) == (1, 1, 0)
    assert first["state"] == "running"
    assert (first["job"]["kind"], first["job"]["dataset_name"]) == ("map", "뉴스")
    assert body["slots"][1]["job"] is None


async def test_get_workers_shows_draining_slot_written_by_supervisor(client, db_session):
    # 준비: 관리 프로세스(이 테스트 프로세스)가 실행기 넷 가운데 3 · 4를 줄이는 중이라고 적었다
    settings = get_settings()
    workers_service.write_pool_state(
        settings,
        slots={
            1: SlotState.RUNNING,
            2: SlotState.RUNNING,
            3: SlotState.DRAINING,
            4: SlotState.DRAINING,
        },
    )
    try:
        # 실행
        response = await client.put(API, json={"count": 2})
    finally:
        workers_service.remove_pool_state(settings)

    # 확인: 정한 수보다 큰 번호도 끝날 때까지 보인다
    body = response.json()
    assert body["managed"] is True
    assert [slot["state"] for slot in body["slots"]] == [
        "stopped",
        "stopped",
        "draining",
        "draining",
    ]


async def test_read_pool_state_ignores_file_of_finished_supervisor(db_session):
    # 준비: 끝난 관리 프로세스가 남긴 파일 (만든 시각이 다르다)
    settings = get_settings()
    workers_service.write_pool_state(settings, slots={1: SlotState.RUNNING})
    path = workers_service.pool_file(settings)
    created = psutil.Process(os.getpid()).create_time()
    path.write_text(
        path.read_text(encoding="utf-8").replace(str(created), str(created - 100)),
        encoding="utf-8",
    )
    try:
        # 실행
        found = workers_service.read_pool_state(settings)
    finally:
        workers_service.remove_pool_state(settings)

    # 확인
    assert found is None
