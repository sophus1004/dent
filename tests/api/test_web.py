"""화면 파일을 내주는 곳(api/web.py) 테스트."""

import httpx
from fastapi import FastAPI, status

from dent.api.web import mount_web

# 가짜 화면 폴더에 넣는 파일 내용
INDEX_HTML = "<!doctype html><title>DENT</title>"
APP_JS = "console.log('dent')"


def _web_client(app: FastAPI) -> httpx.AsyncClient:
    """주어진 앱을 포트 없이 바로 부르는 클라이언트."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_index_returns_html_without_cache(client):
    # 실행
    response = await client.get("/")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


async def test_unknown_api_path_returns_json_404_not_the_screen(client):
    # 실행
    response = await client.get("/api/v1/does-not-exist")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


async def test_javascript_is_served_as_javascript_when_web_is_built(tmp_path):
    # 준비
    assets = tmp_path / "dist" / "assets"
    assets.mkdir(parents=True)
    (tmp_path / "dist" / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (assets / "app.js").write_text(APP_JS, encoding="utf-8")
    app = FastAPI()
    mount_web(app, tmp_path)

    # 실행
    async with _web_client(app) as http:
        response = await http.get("/assets/app.js")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/javascript")
    assert "cache-control" not in response.headers


async def test_placeholder_is_served_when_web_is_not_built(tmp_path):
    # 준비
    placeholder = tmp_path / "placeholder"
    placeholder.mkdir()
    (placeholder / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    app = FastAPI()
    mount_web(app, tmp_path)

    # 실행
    async with _web_client(app) as http:
        response = await http.get("/")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.text == INDEX_HTML
    assert response.headers["cache-control"] == "no-cache"
