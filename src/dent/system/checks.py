"""실행할 때 거치는 확인: storage, DB 연결, pgvector, 테이블 버전.

멈춰야 하는 문제는 StartupError로 알린다. 알리기만 할 문제는 경고 로그로 남긴다.
API 서버의 /readyz도 같은 함수를 한 번씩만 시도해서 쓴다.
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from dent.system.config import PROJECT_ROOT, Settings
from dent.system.exceptions import StartupError

logger = logging.getLogger(__name__)

# storage 아래에 늘 있어야 하는 폴더
STORAGE_SUBDIRS = ("uploads", "snapshots", "exports", "logs")

# 디스크가 이보다 적게 남으면 경고한다. 큰 데이터를 가져오면 모자랄 수 있다.
LOW_DISK_BYTES = 10 * 1024**3

# DB에 붙지 못할 때 다시 시도하는 횟수와 간격. DB 서버가 막 실행되는 중일 수 있다.
DB_CONNECT_ATTEMPTS = 5
DB_CONNECT_INTERVAL_S = 2.0

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
UPGRADE_COMMAND = "uv run alembic upgrade head"

# DB에 붙을 때 날 수 있는 예외: SQLAlchemy가 감싼 드라이버 오류, 연결 거부, 시간 초과
CONNECT_ERRORS = (
    SQLAlchemyError,
    OSError,
    TimeoutError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
)


@dataclass(frozen=True)
class StorageInfo:
    """storage 확인 결과."""

    # storage 폴더의 절대 경로
    path: Path

    # 남은 디스크 공간(바이트)
    free_bytes: int


@dataclass(frozen=True)
class DatabaseInfo:
    """DB 확인 결과."""

    # 비밀번호를 뺀 'host:port/데이터베이스'
    target: str

    # PostgreSQL 버전 (예: 18.6)
    server_version: str

    # pgvector 버전 (예: 0.8.6)
    vector_version: str

    # DB에 적용된 마이그레이션 버전
    schema_revision: str


def check_storage(settings: Settings, *, warn_low_disk: bool = True) -> StorageInfo:
    """storage 폴더를 만들고 쓰기가 되는지 본다. 쓸 수 없으면 StartupError."""
    root = settings.storage_path
    try:
        for name in STORAGE_SUBDIRS:
            (root / name).mkdir(parents=True, exist_ok=True)
        probe = root / ".write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        raise StartupError(f"storage 폴더에 쓸 수 없습니다: {root}. 권한을 확인하세요.") from None

    free_bytes = shutil.disk_usage(root).free
    is_low_disk = free_bytes < LOW_DISK_BYTES
    if is_low_disk and warn_low_disk:
        logger.warning(
            "디스크 여유가 %dGB입니다. 큰 데이터를 가져오면 모자랄 수 있습니다.",
            free_bytes // 1024**3,
        )
    return StorageInfo(path=root, free_bytes=free_bytes)


async def check_database(
    engine: AsyncEngine, settings: Settings, *, attempts: int = DB_CONNECT_ATTEMPTS
) -> DatabaseInfo:
    """DB에 붙고, vector 확장과 테이블 버전을 본다. 하나라도 안 되면 StartupError."""
    target = settings.database_target()
    server_version = await connect_database(engine, target=target, attempts=attempts)
    async with engine.connect() as conn:
        vector_version = await check_vector(conn, target=target)
        schema_revision = await check_schema(conn)
    return DatabaseInfo(
        target=target,
        server_version=server_version,
        vector_version=vector_version,
        schema_revision=schema_revision,
    )


async def connect_database(engine: AsyncEngine, *, target: str, attempts: int) -> str:
    """DB에 붙어 PostgreSQL 버전을 돌려준다. 붙지 못하면 간격을 두고 다시 시도한다."""
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                version = await conn.scalar(text("SHOW server_version"))
                return str(version).split()[0]
        except CONNECT_ERRORS as error:
            # 드라이버 예외가 SQLAlchemy 예외에 싸여 오므로 원인을 거슬러 올라가 본다
            not_retryable = _describe_not_retryable(error, target=target)
            if not_retryable:
                raise StartupError(not_retryable) from None
            if attempt == attempts:
                raise StartupError(
                    f"DB에 붙지 못했습니다 ({target}). "
                    "DB 서버가 실행 중인지, .env의 DATABASE_URL이 맞는지 확인하세요."
                ) from None
            logger.info(
                "DB에 붙지 못해 %.0f초 뒤 다시 시도합니다 (%d/%d).",
                DB_CONNECT_INTERVAL_S,
                attempt,
                attempts,
            )
            await asyncio.sleep(DB_CONNECT_INTERVAL_S)
    raise StartupError(f"DB에 붙지 못했습니다 ({target}).")


async def check_vector(conn: AsyncConnection, *, target: str) -> str:
    """vector 확장이 실행 중이면 그 버전을 돌려준다. 없으면 StartupError."""
    version = await conn.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    if version is None:
        database = target.rsplit("/", 1)[-1]
        raise StartupError(
            f"{database} 데이터베이스에 vector 확장이 없습니다. "
            "관리자 계정으로 CREATE EXTENSION vector; 를 실행하세요."
        )
    return str(version)


async def check_schema(conn: AsyncConnection) -> str:
    """DB의 마이그레이션 버전이 코드가 기대하는 최신(head)인지 본다. 테이블을 스스로 바꾸지 않는다."""
    has_version_table = await conn.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
    current = None
    if has_version_table:
        current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    if current is None:
        raise StartupError(f"테이블이 아직 없습니다. {UPGRADE_COMMAND} 를 실행하세요.")

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    is_latest = current in script.get_heads()
    if is_latest:
        return str(current)

    # 코드가 아는 버전이면 DB가 옛것이고, 모르는 버전이면 DB가 코드보다 새것이다.
    try:
        script.get_revision(current)
    except (RevisionError, CommandError):
        raise StartupError(
            "DB가 이 프로그램보다 새 버전입니다. 프로그램을 업데이트하세요."
        ) from None
    raise StartupError(f"테이블을 새 버전으로 바꿔야 합니다. {UPGRADE_COMMAND} 를 실행하세요.")


def _describe_not_retryable(error: BaseException, *, target: str) -> str | None:
    """다시 시도해도 풀리지 않는 접속 오류(비밀번호, 사용자, 데이터베이스 이름)면 안내 문장을 돌려준다."""
    database = target.rsplit("/", 1)[-1]
    for cause in _causes(error):
        if isinstance(cause, asyncpg.exceptions.InvalidPasswordError):
            return "DB 접속 비밀번호가 맞지 않습니다. .env의 DATABASE_URL을 확인하세요."
        if isinstance(cause, asyncpg.exceptions.InvalidAuthorizationSpecificationError):
            return (
                "DB 사용자가 없거나 접속이 허락되지 않았습니다. "
                ".env의 DATABASE_URL과 DB 서버의 사용자 설정을 확인하세요."
            )
        if isinstance(cause, asyncpg.exceptions.InvalidCatalogNameError):
            return f"{database} 데이터베이스가 없습니다. DB 서버에 먼저 만드세요."
    return None


def _causes(error: BaseException) -> list[BaseException]:
    """예외가 감싸고 있는 원인들을 차례로 모은다(SQLAlchemy는 드라이버 예외를 감싼다)."""
    found: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in found:
        found.append(current)
        current = getattr(current, "orig", None) or current.__cause__ or current.__context__
    return found
