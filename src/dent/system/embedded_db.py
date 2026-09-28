"""내장 DB: .env에 DATABASE_URL이 없으면 쓰는 PostgreSQL + pgvector.

패키지(embedded-postgres)에 든 PostgreSQL 실행 파일로 storage/pgdata에 DB를 만들어 실행하고 종료한다. 이 PC 안에서만 붙는다
(Mac · Linux는 소켓 파일, Windows는 127.0.0.1의 빈 포트). 외부 DB와 엔진이 같아서 나머지 코드는 차이를 모른다.

- 실행 · 붙기(ensure_running_at): 데이터 폴더가 없으면 만들고, 서버가 종료돼 있으면 실행하고, 이미 떠 있으면 그 서버에 붙는다.
  런처 · API 서버 · 작업 실행기 · alembic이 같은 서버를 쓴다. DENT가 쓰는 데이터베이스와 vector 확장도 없으면 만든다.
- 종료(stop_at): 런처가 종료할 때 pg_ctl로 종료한다. 패키지의 '마지막으로 쓰던 프로세스가 끝나면 종료'는 프로세스가 한 번
  강제로 끝나면 종료하지 못해서 쓰지 않는다. 강제로 끝나 서버가 남아 있으면 다음에 실행할 때 그 서버에 붙는다.
"""

import subprocess
from pathlib import Path

import embedded_postgres

from dent.system.config import Settings
from dent.system.exceptions import StartupError

# DENT가 쓰는 데이터베이스 이름 (외부 DB 예시와 같다)
DATABASE_NAME = "dent"

# 패키지가 주는 주소의 앞머리와, DENT(SQLAlchemy · asyncpg)가 쓰는 앞머리
PACKAGE_URL_PREFIX = "postgresql://"
DRIVER_URL_PREFIX = "postgresql+asyncpg://"

START_FAILED_MESSAGE = (
    "내장 DB를 실행하지 못했습니다. storage의 pgdata/log를 확인하세요. "
    "관리자(root · Administrator) 권한으로 실행하면 PostgreSQL이 거부합니다."
)

# 실행하거나 붙은 서버의 주소 (데이터 폴더마다). 한 프로세스에서 여러 번 물어도 한 번만 확인한다.
_urls: dict[Path, str] = {}


def ensure_running(settings: Settings) -> str:
    """설정의 storage에 있는 내장 DB를 실행하거나 떠 있는 서버에 붙고 DENT가 쓸 주소를 돌려준다. 안 되면 StartupError."""
    return ensure_running_at(settings.embedded_database_path)


def ensure_running_at(pgdata: Path, *, database: str = DATABASE_NAME) -> str:
    """pgdata의 내장 DB를 실행하거나 떠 있는 서버에 붙고, 데이터베이스 · vector 확장을 갖춘 뒤 주소를 돌려준다.

    안 되면 StartupError (테스트는 임시 폴더로 부른다).
    """
    pgdata = pgdata.expanduser().resolve()
    cached = _urls.get(pgdata)
    if cached is not None and (pgdata / "postmaster.pid").exists():
        return cached
    try:
        pgdata.parent.mkdir(parents=True, exist_ok=True)
        server = embedded_postgres.get_server(pgdata, cleanup_mode=None)
        _ensure_database(server.get_uri(), database=database)
        _run_sql(server.get_uri(database), "CREATE EXTENSION IF NOT EXISTS vector")
        url = server.get_uri(database).replace(PACKAGE_URL_PREFIX, DRIVER_URL_PREFIX, 1)
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        raise StartupError(START_FAILED_MESSAGE) from None
    _urls[pgdata] = url
    return url


def database_version(settings: Settings) -> str:
    """실행 중인 내장 DB의 PostgreSQL 버전(예: '18.6'). 모르면 ''. 실행 화면에 보인다."""
    try:
        uri = ensure_running(settings).replace(DRIVER_URL_PREFIX, PACKAGE_URL_PREFIX, 1)
        return _run_sql(uri, "SHOW server_version").strip().split(" ")[0]
    except (StartupError, OSError, RuntimeError, subprocess.CalledProcessError):
        return ""


def stop(settings: Settings) -> bool:
    """설정의 storage에 있는 내장 DB가 떠 있으면 종료한다. 종료 여부."""
    return stop_at(settings.embedded_database_path)


def stop_at(pgdata: Path) -> bool:
    """pgdata의 내장 DB가 떠 있으면 종료한다(빠른 종료: 연결을 끊고 저장한 뒤 종료한다). 종료 여부."""
    pgdata = pgdata.expanduser().resolve()
    _urls.pop(pgdata, None)
    is_running = (pgdata / "postmaster.pid").exists()
    if not is_running:
        return False
    try:
        embedded_postgres.pg_ctl(["-w", "-m", "fast", "stop"], pgdata=pgdata)
    except subprocess.CalledProcessError:
        return False
    return True


def _ensure_database(admin_uri: str, *, database: str) -> None:
    """데이터베이스가 없으면 만든다 (서버를 처음 만든 뒤 한 번)."""
    found = _run_sql(admin_uri, f"SELECT 1 FROM pg_database WHERE datname = '{database}'")
    if not found.strip():
        _run_sql(admin_uri, f'CREATE DATABASE "{database}"')


def _run_sql(uri: str, sql: str) -> str:
    """패키지에 든 psql로 SQL 한 문을 돌리고 결과 글을 돌려준다. SQL이 틀리면 CalledProcessError."""
    return embedded_postgres.psql([uri, "-v", "ON_ERROR_STOP=1", "-tAc", sql])
