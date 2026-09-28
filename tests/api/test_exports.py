"""내보낸 파일 API 테스트: 목록 · 하나 · 내려받기(404 포함)."""

from fastapi import status

from dent.system import exports
from dent.system.models import Dataset

API = "/api/v1/exports"


async def _done_export(db_session):
    dataset = Dataset(module="retrieval", name="검색")
    db_session.add(dataset)
    await db_session.flush()
    export = await exports.create_export(
        db_session,
        module="retrieval",
        dataset_id=dataset.id,
        format="train_jsonl",
        file_name="train.jsonl",
        options={},
    )
    exports.start_export(export)
    exports.file_path(export).write_text('{"query": "환불"}\n', encoding="utf-8")
    await exports.finish_export(db_session, export, job_id=export.job_id, result={"lines": 1})
    await db_session.commit()
    return export


async def test_list_exports_returns_dataset_exports(client, db_session):
    # 준비
    export = await _done_export(db_session)

    # 실행
    response = await client.get(API, params={"dataset_id": export.dataset_id})

    # 확인
    assert response.status_code == status.HTTP_200_OK
    [item] = response.json()
    assert item["id"] == export.id
    assert item["status"] == "done"
    assert item["file_name"] == "train.jsonl"


async def test_download_export_returns_file(client, db_session):
    # 준비
    export = await _done_export(db_session)

    # 실행
    response = await client.get(f"{API}/{export.id}/file")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.text == '{"query": "환불"}\n'
    assert "train.jsonl" in response.headers["content-disposition"]


async def test_get_export_returns_404_when_missing(client):
    # 실행
    response = await client.get(f"{API}/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "내보내기 기록을 찾을 수 없습니다."}


async def test_download_export_returns_404_after_retention(client, db_session):
    # 준비
    export = await _done_export(db_session)
    export.created_at = export.created_at - exports.EXPORT_RETENTION * 2
    await db_session.commit()
    await exports.discard_expired_exports(db_session)
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/{export.id}/file")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
