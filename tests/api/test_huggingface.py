"""허깅페이스 API 테스트. 인터넷 대신 가짜 전송 계층을 끼워 넣는다."""

import httpx
from fastapi import status

from dent.api.huggingface import get_http_transport
from dent.main import app
from tests.system.helpers import HF_REPO, huggingface_transport

API = "/api/v1/huggingface/preview"


async def _preview(client, body: dict[str, object], transport: httpx.AsyncBaseTransport):
    """가짜 전송 계층으로 미리 보기를 부른다."""
    app.dependency_overrides[get_http_transport] = lambda: transport
    try:
        return await client.post(API, json=body)
    finally:
        app.dependency_overrides.clear()


async def test_preview_huggingface_returns_splits_and_label_names(client):
    # 실행
    response = await _preview(client, {"repo": HF_REPO}, huggingface_transport())

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["config"], body["split"]) == ("default", "train")
    assert body["splits"] == [
        {"name": "train", "num_rows": 3},
        {"name": "validation", "num_rows": 1},
    ]
    assert body["label_names"] == {"label": ["부정", "긍정"]}
    assert body["rows"][0] == {"text": "좋아요", "label": "긍정"}


async def test_preview_huggingface_returns_502_when_offline(client):
    # 준비
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("연결 거부", request=request)

    # 실행
    response = await _preview(client, {"repo": "a/b"}, httpx.MockTransport(refuse))

    # 확인
    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert response.json() == {"detail": "허깅페이스에 붙지 못했습니다. 인터넷 연결을 확인하세요."}


async def test_preview_huggingface_returns_404_when_dataset_is_missing(client):
    # 준비
    missing = httpx.MockTransport(lambda _request: httpx.Response(404, json={"error": "없음"}))

    # 실행
    response = await _preview(client, {"repo": "a/b"}, missing)

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "허깅페이스에서 데이터셋을 찾지 못했습니다: a/b"}


async def test_preview_huggingface_returns_422_when_repo_is_blank(client):
    # 실행
    response = await client.post(API, json={"repo": "   "})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
