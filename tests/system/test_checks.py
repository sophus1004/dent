"""실행할 때 확인 테스트. DB 관련 테스트는 테스트 DB(dent_test)에 붙는다."""

import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from dent.system.checks import (
    ALEMBIC_INI,
    STORAGE_SUBDIRS,
    check_database,
    check_schema,
    check_storage,
)
from dent.system.config import Settings, get_settings
from dent.system.db import get_engine
from dent.system.exceptions import StartupError

# 내장 테스트 DB는 이 PC 안의 연결을 비밀번호 없이 받아 비밀번호 검사를 잴 수 없다(conftest가 알린다).
IS_EMBEDDED_TEST_DB = os.environ.get("DENT_TEST_EMBEDDED_DB") == "1"


def _test_database_url(**changes: object) -> str:
    """테스트 DB 주소에서 일부만 바꾼 주소."""
    url = make_url(get_settings().database_url.get_secret_value()).set(**changes)
    return url.render_as_string(hide_password=False)


async def _check_with_url(url: str) -> StartupError:
    """주어진 주소로 한 번만 확인해 보고, 난 StartupError를 돌려준다."""
    engine = create_async_engine(url)
    try:
        with pytest.raises(StartupError) as caught:
            await check_database(engine, Settings(_env_file=None, database_url=url), attempts=1)
    finally:
        await engine.dispose()
    return caught.value


def test_check_storage_creates_missing_folders(tmp_path):
    # 준비
    settings = Settings(
        _env_file=None, database_url=_test_database_url(), storage_dir=tmp_path / "storage"
    )

    # 실행
    info = check_storage(settings)

    # 확인
    assert all((info.path / name).is_dir() for name in STORAGE_SUBDIRS)


async def test_check_database_passes_on_migrated_test_database():
    # 준비
    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()

    # 실행
    info = await check_database(get_engine(), get_settings(), attempts=1)

    # 확인
    assert info.target.endswith("/dent_test")
    assert info.vector_version
    assert info.schema_revision in heads


@pytest.mark.skipif(IS_EMBEDDED_TEST_DB, reason="내장 테스트 DB는 비밀번호를 검사하지 않는다")
async def test_check_database_fails_when_password_is_wrong():
    # 실행
    error = await _check_with_url(_test_database_url(password="wrong-password"))

    # 확인
    assert "비밀번호가 맞지 않습니다" in error.message


async def test_check_database_fails_when_database_is_missing():
    # 실행
    error = await _check_with_url(_test_database_url(database="dent_missing"))

    # 확인
    assert "dent_missing 데이터베이스가 없습니다" in error.message


async def test_check_database_fails_when_server_is_unreachable():
    # 실행: 아무도 듣지 않는 포트
    error = await _check_with_url(_test_database_url(port=1))

    # 확인
    assert "DB에 붙지 못했습니다" in error.message


async def test_check_schema_fails_when_database_is_newer_than_code():
    # 준비: 트랜잭션 안에서만 버전을 코드가 모르는 값으로 바꿨다가 되돌린다
    async with get_engine().connect() as conn:
        transaction = await conn.begin()
        await conn.execute(text("UPDATE alembic_version SET version_num = 'ffffffffffff'"))

        # 실행
        with pytest.raises(StartupError) as caught:
            await check_schema(conn)
        await transaction.rollback()

    # 확인
    assert "새 버전" in caught.value.message
