"""상태 확인 주소 테스트."""

from importlib.metadata import version

from fastapi import status


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
