"""DB 연결. 엔진 하나를 만들어 두고, 쓸 때마다 세션을 하나 열고 닫는다.

엔진은 이벤트 루프에 묶인다. 실행할 때 확인을 한 루프에서 하고 서버를 다른 루프에서 돌리면,
그 사이에 dispose_engine()으로 닫았다가 새 루프에서 다시 만든다.
시스템과 모듈의 테이블이 함께 쓰는 도구(제약 이름 규칙, CHECK 식, 이름 겹침 잡기)도 여기에 둔다.
"""

from collections.abc import AsyncIterator
from enum import StrEnum

from sqlalchemy import MetaData
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from dent.system import embedded_db
from dent.system.config import Settings, get_settings
from dent.system.exceptions import ConflictError

# 제약·인덱스 이름을 규칙대로 붙인다. Alembic이 이름으로 찾아 바꾸거나 지울 수 있게.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# API 연결의 쿼리 시간 한도(밀리초). 넘는 일은 작업 실행기로 보낸다.
API_STATEMENT_TIMEOUT_MS = 30_000

# DB 서버에 붙을 때 기다리는 최대 시간(초). 주소가 틀렸을 때 오래 멈춰 있지 않게.
CONNECT_TIMEOUT_S = 5


class Base(DeclarativeBase):
    """모든 테이블 모델의 부모."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(*, statement_timeout_ms: int | None = API_STATEMENT_TIMEOUT_MS) -> AsyncEngine:
    """엔진을 만든다. 이미 있으면 그대로 돌려준다. 작업 실행기는 시간 한도 없이(None) 만든다."""
    global _engine, _sessionmaker
    if _engine is None:
        server_settings = {"application_name": "dent"}
        if statement_timeout_ms is not None:
            server_settings["statement_timeout"] = str(statement_timeout_ms)
        _engine = create_async_engine(
            resolve_database_url(get_settings()),
            pool_pre_ping=True,
            connect_args={"timeout": CONNECT_TIMEOUT_S, "server_settings": server_settings},
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def resolve_database_url(settings: Settings) -> str:
    """DENT가 붙을 DB 주소: .env의 DATABASE_URL, 없으면 내장 DB(실행하거나 떠 있는 서버에 붙는다). 안 되면 StartupError."""
    if settings.database_url is not None:
        return settings.database_url.get_secret_value()
    return embedded_db.ensure_running(settings)


def get_engine() -> AsyncEngine:
    """엔진을 돌려준다. 없으면 API 기본값으로 만든다."""
    return init_engine()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """세션을 만드는 함수를 돌려준다. 엔진이 없으면 API 기본값으로 만든다."""
    init_engine()
    if _sessionmaker is None:
        raise RuntimeError("DB 엔진이 준비되지 않았습니다.")
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """요청마다 세션 하나를 열고, 요청이 끝나면 닫는다."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """엔진과 연결을 모두 닫는다. 다른 이벤트 루프에서 다시 쓰려면 먼저 닫아야 한다."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def check_in(column: str, values: type[StrEnum]) -> str:
    """'column IN (...)' CHECK 식을 만든다. 상태 같은 고정된 값을 text 칸에 둘 때 쓴다."""
    allowed = ", ".join(f"'{value.value}'" for value in values)
    return f"{column} IN ({allowed})"


async def flush_or_conflict(db: AsyncSession, *, message: str) -> None:
    """바꾼 값을 DB에 보낸다. 같은 이름이 동시에 들어와 UNIQUE 제약에 걸리면 ConflictError.

    미리 확인했더라도 두 요청이 동시에 오면 둘 다 통과할 수 있다. DB 제약이 마지막 방어선이다.
    제약은 줄을 DB에 보낼 때(flush) 걸린다. 그 뒤의 UPDATE(수정 시각 적기 등)가 저절로
    flush하기 전에 여기서 먼저 보내야 잡을 수 있다.
    """
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(message) from None
