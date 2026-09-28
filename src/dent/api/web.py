"""웹 화면 파일을 내준다.

화면을 빌드한 결과(web/dist)가 있으면 그것을, 아직 없으면 켜졌는지만 보여 주는
임시 화면(web/placeholder)을 내준다. API 주소보다 뒤에 붙여야 API가 가려지지 않는다.
화면 주소는 # 뒤에 있으므로(#/classification/3) 서버는 index.html 하나만 내주면 되고,
없는 주소(/api/v1/없는-것 포함)는 화면 대신 404 JSON을 돌려준다.
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from dent.system.config import PROJECT_ROOT

WEB_DIR = PROJECT_ROOT / "web"

# 파일 종류는 운영체제의 설정에서 읽는다. Windows는 .js를 text/plain으로 알려 주기도 하는데,
# 그러면 브라우저가 화면 코드를 실행하지 않는다. 화면에 쓰는 종류는 여기서 못 박아 둔다.
WEB_MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
}
for _extension, _media_type in WEB_MEDIA_TYPES.items():
    mimetypes.add_type(_media_type, _extension)

# html에 붙이는 캐시 규칙. 브라우저가 쓰기 전에 늘 새것인지 물어보게 해서,
# 화면을 다시 빌드하면 새로고침만으로 바뀐 화면이 보인다. js·css는 이름에 내용 해시가 있어 그대로 둔다.
HTML_CACHE_CONTROL = "no-cache"


class WebFiles(StaticFiles):
    """화면 파일. html 답에만 no-cache를 붙인다."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """파일을 찾아 돌려준다. 없으면 StaticFiles가 404를 낸다."""
        response = await super().get_response(path, scope)
        is_html = response.headers.get("content-type", "").startswith("text/html")
        if is_html:
            response.headers["Cache-Control"] = HTML_CACHE_CONTROL
        return response


def mount_web(app: FastAPI, web_dir: Path = WEB_DIR) -> None:
    """화면 파일을 / 에 붙인다. 빌드한 화면이 있으면 그것을, 없으면 임시 화면을 쓴다."""
    built = web_dir / "dist"
    has_built_web = (built / "index.html").exists()
    directory = built if has_built_web else web_dir / "placeholder"
    app.mount("/", WebFiles(directory=directory, html=True), name="web")
