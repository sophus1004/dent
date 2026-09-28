"""올린 파일 API 테스트: 주소, 상태 코드, 응답 모양, 오류 문장."""

import httpx
from fastapi import status

from dent.system import uploads as uploads_service
from tests.system.helpers import xlsx_bytes

API = "/api/v1/uploads"


async def _upload(client, file_name: str, content: bytes) -> httpx.Response:
    """파일 하나를 multipart로 올린다."""
    return await client.post(API, files={"file": (file_name, content)})


async def test_upload_file_returns_201_with_preview(client, db_session):
    # 실행
    response = await _upload(client, "인사.csv", "text,label\n안녕,인사\n".encode())

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_201_CREATED
    assert len(body["upload_id"]) == 32
    assert (body["file_name"], body["format"], body["row_count"]) == ("인사.csv", "csv", 1)
    assert (body["columns"], body["rows"], body["lines"]) == (
        ["text", "label"],
        [{"text": "안녕", "label": "인사"}],
        [2],
    )
    # 필드 맞춤 짐작은 모듈의 일이라 시스템 미리 보기에는 없다.
    assert "suggested_mapping" not in body


async def test_upload_file_returns_422_when_format_is_not_supported(client, db_session):
    # 실행
    response = await _upload(client, "메모.txt", b"hello")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {
        "detail": "지원하지 않는 형식입니다. .csv 또는 .xlsx 파일을 올려 주세요."
    }


async def test_preview_upload_returns_other_sheet(client, db_session):
    # 준비
    uploaded = (await _upload(client, "두 시트.xlsx", xlsx_bytes())).json()

    # 실행
    response = await client.get(f"{API}/{uploaded['upload_id']}/preview", params={"sheet": "둘째"})

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["sheet"], body["columns"], body["row_count"]) == (
        "둘째",
        ["문장", "의도", "번호"],
        3,
    )


async def test_preview_upload_returns_404_when_upload_is_missing(client, db_session):
    # 실행
    response = await client.get(f"{API}/{'a' * 32}/preview")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "올린 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요."}


async def test_preview_upload_returns_404_when_upload_is_discarded(client, db_session):
    # 준비: 가져오기가 끝나 원본 파일을 지웠다.
    uploaded = (await _upload(client, "인사.csv", "text,label\n안녕,인사\n".encode())).json()
    await uploads_service.discard_upload(db_session, upload_id=uploaded["upload_id"])
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/{uploaded['upload_id']}/preview")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "올린 파일이 지워졌습니다. 다시 올려 주세요."}


async def test_preview_upload_returns_404_when_sheet_is_missing(client, db_session):
    # 준비
    uploaded = (await _upload(client, "두 시트.xlsx", xlsx_bytes())).json()

    # 실행
    response = await client.get(f"{API}/{uploaded['upload_id']}/preview", params={"sheet": "셋째"})

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "'셋째' 시트가 없습니다."}
