"""설정 읽기 테스트."""

import pytest
from pydantic import ValidationError

from dent.system.config import PROJECT_ROOT, Settings, describe_settings_error

DATABASE_URL = "postgresql+asyncpg://user:secret@127.0.0.1:5432/somedb"


def test_settings_rejects_host_other_than_loopback():
    # 실행
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=DATABASE_URL, host="0.0.0.0")

    # 확인
    assert "127.0.0.1만" in describe_settings_error(caught.value)


def test_settings_uses_embedded_database_when_url_is_missing(monkeypatch):
    # 준비
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # 실행
    settings = Settings(_env_file=None, storage_dir="storage")

    # 확인: 설정 없이도 실행되고, 내장 DB의 데이터 폴더는 storage/pgdata다.
    assert settings.uses_embedded_database
    assert settings.embedded_database_path == PROJECT_ROOT / "storage" / "pgdata"
    assert f"DB 내장 · {PROJECT_ROOT / 'storage' / 'pgdata'}" in settings.describe()


def test_settings_treats_blank_database_url_as_embedded():
    # 실행
    settings = Settings(_env_file=None, database_url="  ")

    # 확인
    assert settings.uses_embedded_database


def test_describe_shows_socket_folder_for_socket_database_url():
    # 실행
    summary = Settings(
        _env_file=None, database_url="postgresql+asyncpg://postgres:@/dent?host=/tmp/pg"
    ).describe()

    # 확인
    assert "DB socket /tmp/pg/dent" in summary


def test_storage_path_is_resolved_from_project_root():
    # 실행
    settings = Settings(_env_file=None, database_url=DATABASE_URL, storage_dir="storage")

    # 확인
    assert settings.storage_path == PROJECT_ROOT / "storage"


def test_describe_hides_database_password():
    # 실행
    summary = Settings(_env_file=None, database_url=DATABASE_URL).describe()

    # 확인
    assert "secret" not in summary
    assert "127.0.0.1:5432/somedb" in summary


def test_settings_places_embedded_models_after_port_and_in_storage():
    # 실행
    settings = Settings(_env_file=None, port=8100, storage_dir="storage")

    # 확인
    assert (settings.embedded_embedding_port, settings.embedded_jev_port) == (8101, 8102)
    assert settings.embedded_models_path == PROJECT_ROOT / "storage" / "models"
