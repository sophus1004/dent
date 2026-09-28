"""Alembic 실행 환경. DB 주소는 .env의 DATABASE_URL에서(없으면 내장 DB를 켜거나 떠 있는 서버에 붙어서),
테이블 목록은 dent.models에서 읽는다."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import dent.models  # noqa: F401  모든 테이블 모델을 Base.metadata에 올린다
from dent.system.config import get_settings
from dent.system.db import Base, resolve_database_url

config = context.config

# 명령줄에서 부를 때는 alembic.ini의 로그 설정을 쓴다. 다른 로거는 끄지 않는다.
# 런처가 내장 DB의 테이블을 올릴 때는 켤 때마다 로그가 찍히지 않게 건너뛴다(configure_logger=False).
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# configparser가 %를 특수 문자로 읽으므로 %%로 바꿔 넘긴다.
database_url = resolve_database_url(get_settings())
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB에 붙지 않고 SQL 문만 만들어 보여 준다 (alembic upgrade --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """붙은 연결로 마이그레이션을 적용한다."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """비동기 엔진으로 붙어 마이그레이션을 적용한다."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """DB에 붙어 마이그레이션을 적용한다."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
