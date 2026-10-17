"""상태 확인 주소 테스트."""

from importlib.metadata import version

from fastapi import status

from dent.system import jobs
from dent.system.db import get_engine


async def test_healthz_returns_app_name_and_installed_version(client):
    # 실행
    response = await client.get("/healthz")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"app": "dent", "version": version("dent")}


async def test_readyz_is_ready_when_database_is_migrated(client):
    # 실행
    response = await client.get("/readyz")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert body["ready"] is True
    assert body["checks"]["db"]["ok"]
    assert body["checks"]["schema"]["ok"]


async def test_readyz_reports_worker_off_when_no_worker_holds_the_lock(client):
    # 실행
    response = await client.get("/readyz")

    # 확인
    worker = response.json()["info"]["worker"]
    assert worker["ok"] is False
    assert "실행 중이 아닙니다" in worker["detail"]


async def test_readyz_reports_running_workers_out_of_count(client, db_session):
    # 준비: 실행기 넷 가운데 하나가 자리를 잡았다
    async with get_engine().connect() as lock_conn:
        await jobs.try_acquire_worker_lock(lock_conn, slot=1)

        # 실행
        response = await client.get("/readyz")
        await jobs.release_worker_lock(lock_conn, slot=1)

    # 확인
    worker = response.json()["info"]["worker"]
    assert worker["ok"] is True
    assert worker["detail"] == "실행 중 1 / 4 · 도는 중 0 · 대기 작업 0건"
