"""작업 API 테스트: 하나 읽기, 목록(최근 순 · 거르기 · 상태별 수)."""

from datetime import UTC, datetime, timedelta

from fastapi import status

from dent.system import datasets as datasets_service
from dent.system.models import Job, JobStatus


async def test_get_job_returns_status_and_progress(client, db_session):
    # 준비
    job = Job(module="classification", kind="import", params={"import_id": 1}, progress_total=10)
    db_session.add(job)
    await db_session.commit()

    # 실행
    response = await client.get(f"/api/v1/jobs/{job.id}")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["module"], body["kind"], body["status"]) == ("classification", "import", "queued")
    assert (body["progress_done"], body["progress_total"]) == (0, 10)
    assert (body["result"], body["error"], body["started_at"]) == (None, None, None)


async def test_get_job_returns_404_when_missing(client, db_session):
    # 실행
    response = await client.get("/api/v1/jobs/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "작업을 찾을 수 없습니다."}


async def _add_jobs(db_session, *specs: tuple[str, str, JobStatus]) -> list[Job]:
    """(모듈, 종류, 상태)마다 작업 한 줄을 넣는다. 넣은 순서대로 1초씩 늦게 넣은 것으로 적는다."""
    start = datetime.now(UTC) - timedelta(hours=1)
    rows = [
        Job(
            module=module, kind=kind, status=job_status, created_at=start + timedelta(seconds=index)
        )
        for index, (module, kind, job_status) in enumerate(specs)
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return rows


async def test_list_jobs_returns_newest_first_with_counts_by_status(client, db_session):
    # 준비
    rows = await _add_jobs(
        db_session,
        ("classification", "import", JobStatus.DONE),
        ("classification", "map", JobStatus.FAILED),
        ("system", "cleanup", JobStatus.DONE),
    )

    # 실행
    response = await client.get("/api/v1/jobs")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert [item["id"] for item in body["items"]] == [rows[2].id, rows[1].id, rows[0].id]
    assert body["total"] == 3
    assert body["counts"] == {"queued": 0, "running": 0, "done": 2, "failed": 1, "canceled": 0}
    assert body["items"][0]["events"] == []


async def test_list_jobs_filters_by_status_kind_and_dataset(client, db_session):
    # 준비
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="뉴스"
    )
    other = await datasets_service.create_dataset(db_session, module="classification", name="다른")
    await db_session.commit()
    rows = [
        Job(module="classification", kind="map", status=JobStatus.FAILED, dataset_id=dataset.id),
        Job(module="classification", kind="map", status=JobStatus.CANCELED, dataset_id=dataset.id),
        Job(module="classification", kind="map", status=JobStatus.DONE, dataset_id=dataset.id),
        Job(module="classification", kind="import", status=JobStatus.FAILED, dataset_id=dataset.id),
        Job(module="classification", kind="map", status=JobStatus.FAILED, dataset_id=other.id),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    # 실행
    response = await client.get(
        "/api/v1/jobs",
        params=[
            ("status", "failed"),
            ("status", "canceled"),
            ("kind", "map"),
            ("dataset_id", dataset.id),
        ],
    )

    # 확인
    body = response.json()
    assert sorted(item["id"] for item in body["items"]) == [rows[0].id, rows[1].id]
    assert body["total"] == 2
    # 상태 거르기만 뺀 같은 조건(지도 · 이 데이터셋)의 상태별 수
    assert (body["counts"]["failed"], body["counts"]["canceled"], body["counts"]["done"]) == (
        1,
        1,
        1,
    )


async def test_list_jobs_filters_by_hours_and_finished_after(client, db_session):
    # 준비
    now = datetime.now(UTC)
    old = Job(
        module="classification",
        kind="map",
        status=JobStatus.DONE,
        created_at=now - timedelta(days=2),
        finished_at=now - timedelta(days=2),
    )
    recent = Job(
        module="classification",
        kind="map",
        status=JobStatus.FAILED,
        created_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=1),
    )
    db_session.add_all([old, recent])
    await db_session.commit()

    # 실행
    by_hours = (await client.get("/api/v1/jobs", params={"hours": 24})).json()
    after = (now - timedelta(minutes=2)).isoformat()
    by_finished = (await client.get("/api/v1/jobs", params={"finished_after": after})).json()

    # 확인
    assert [item["id"] for item in by_hours["items"]] == [recent.id]
    assert [item["id"] for item in by_finished["items"]] == [recent.id]


async def test_list_jobs_returns_422_when_limit_is_too_big(client):
    # 실행
    response = await client.get("/api/v1/jobs", params={"limit": 201})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
