"""테스트 공통 준비.

테스트는 .env의 TEST_DATABASE_URL(dent_test)만 쓴다. 없으면 임시 폴더에 내장 DB를 실행해서 쓰고 끝나면 종료하고 지운다
(PostgreSQL을 따로 두지 않아도 테스트가 돈다). dent를 import하기 전에 DATABASE_URL을 테스트 DB로 바꿔 두고,
파일은 임시 폴더에 쓴다. 세션을 시작할 때 테이블을 최신으로 올린다.
"""

import atexit
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]

# 내장 테스트 DB를 쓰는지 테스트에 알리는 환경 변수 (비밀번호 검사처럼 외부 DB에서만 잴 수 있는 테스트가 본다)
EMBEDDED_TEST_DB_ENV = "DENT_TEST_EMBEDDED_DB"

_values = {**dotenv_values(ROOT / ".env"), **os.environ}
_test_database_url = _values.get("TEST_DATABASE_URL")
if _test_database_url and _test_database_url == _values.get("DATABASE_URL"):
    raise RuntimeError(
        "TEST_DATABASE_URL이 DATABASE_URL과 같습니다. 테스트는 개발 DB를 쓰지 않습니다."
    )
if not _test_database_url:
    # 설정을 읽지 않는 모듈이라 DATABASE_URL을 바꾸기 전에 불러도 된다
    from dent.system import embedded_db

    _embedded_test_dir = Path(tempfile.mkdtemp(prefix="dent-test-pg-"))
    _test_database_url = embedded_db.ensure_running_at(
        _embedded_test_dir / "pgdata", database="dent_test"
    )
    os.environ[EMBEDDED_TEST_DB_ENV] = "1"

    def _remove_embedded_test_db() -> None:
        embedded_db.stop_at(_embedded_test_dir / "pgdata")
        shutil.rmtree(_embedded_test_dir, ignore_errors=True)

    atexit.register(_remove_embedded_test_db)
os.environ["DATABASE_URL"] = _test_database_url
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="dent-test-storage-")
# 런처가 넘긴 내장 모델 선택이 남아 있어도 테스트는 쓰지 않는다(쓰는 테스트는 choose_embedded_models로 고른다).
os.environ.pop("DENT_EMBEDDED_MODELS", None)

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import dent.models  # noqa: F401  모든 테이블을 Base.metadata에 올린다
from dent.main import app
from dent.system import status as status_service
from dent.system.checks import ALEMBIC_INI
from dent.system.db import Base, dispose_engine, get_engine, get_sessionmaker
from dent.system.embedded_models import RUN_ENV, ModelChoice, encode_choices
from dent.system.models import ConnectionRole

# 테스트가 끝날 때 비우는 테이블들. 모듈이 늘어도 dent.models 목록을 따라간다.
ALL_TABLES = ", ".join(table.name for table in Base.metadata.sorted_tables)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """테스트 DB의 테이블을 최신으로 올린다. Alembic이 이벤트 루프를 스스로 돌리므로 동기로 부른다."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


@pytest.fixture(scope="session", autouse=True)
async def database(migrated_database: None) -> AsyncIterator[None]:
    """세션 동안 DB 엔진 하나를 쓰고, 끝나면 닫는다."""
    get_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """테스트 하나가 쓸 세션. 끝나면 테스트가 넣은 줄을 모두 지운다."""
    async with get_sessionmaker()() as session:
        yield session
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY"))


@pytest.fixture(autouse=True)
def forget_connection_statuses() -> Iterator[None]:
    """외부 연결 확인 결과의 기억(1분)이 다음 테스트로 넘어가지 않게 지운다."""
    status_service.forget_connection_status()
    yield
    status_service.forget_connection_status()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """API를 부르는 클라이언트. 실제 포트를 열지 않고 앱을 바로 부른다."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def choose_embedded_models(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """실행할 때 내장 모델을 고른 것처럼 한다(런처가 넘기는 환경 변수). 비운 역할은 올리지 않은 것이다."""

    def choose(*, embedding: str = "", jev: str = "", device: str = "cpu") -> None:
        picked = {ConnectionRole.EMBEDDING: embedding, ConnectionRole.JEV: jev}
        choices = [ModelChoice(role=role, device=device) for role, key in picked.items() if key]
        monkeypatch.setenv(RUN_ENV, encode_choices(choices))

    return choose
