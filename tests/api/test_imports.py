"""가져오기 기록 API 테스트."""

from fastapi import status

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system.models import ImportSource

API = "/api/v1/imports"


async def test_get_import_returns_record_with_job(client, db_session):
    # 준비
    dataset = await datasets_service.create_dataset(
        db_session, module="classification", name="상담"
    )
    import_row = await imports_service.create_import(
        db_session,
        module="classification",
        dataset_id=dataset.id,
        source=ImportSource.HUGGINGFACE,
        source_name="klue/klue · ynat",
        options={"repo": "klue/klue", "config": "ynat"},
    )
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/{import_row.id}")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["dataset_id"], body["source"], body["source_name"]) == (
        dataset.id,
        "huggingface",
        "klue/klue · ynat",
    )
    assert (body["status"], body["job_id"], body["rows_total"]) == (
        "queued",
        import_row.job_id,
        None,
    )
    assert (body["skipped_lines"], body["result"], body["error"]) == ([], {}, None)


async def test_get_import_returns_404_when_missing(client, db_session):
    # 실행
    response = await client.get(f"{API}/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "가져오기 기록을 찾을 수 없습니다."}
