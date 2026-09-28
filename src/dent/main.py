"""API 서버 조립: 앱을 만들고 상태 확인, 시스템·모듈 API, 예외 처리기, 화면을 붙인다.

모든 곳을 import해도 되는 조립 파일이다. 실행은 dent.server가 한다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from dent.api import health
from dent.api.error_handlers import register_error_handlers
from dent.api.router import api_router
from dent.api.web import mount_web
from dent.system.config import APP_VERSION
from dent.system.db import dispose_engine, init_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """서버가 요청을 받기 전에 DB 엔진을 만들고, 종료할 때 연결을 모두 닫는다."""
    init_engine()
    yield
    await dispose_engine()


app = FastAPI(
    title="DENT",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
register_error_handlers(app)
app.include_router(health.router)
# 시스템 API(/api/v1/uploads, /api/v1/jobs …)와 모듈 API(/api/v1/classification …)는 api_router가 모은다.
app.include_router(api_router)
mount_web(app)
