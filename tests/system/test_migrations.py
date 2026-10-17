"""마이그레이션 테스트: 모델(Base.metadata)과 마이그레이션으로 올린 테이블이 같은지(uv run alembic check와 같다)."""

from alembic import command
from alembic.config import Config

from dent.system.checks import ALEMBIC_INI


def test_models_match_migrations(migrated_database):
    # 실행 · 확인: 다르면 alembic이 무엇이 다른지와 함께 예외를 낸다
    command.check(Config(str(ALEMBIC_INI)))


def test_latest_migration_downgrades_and_upgrades_again(migrated_database):
    # 준비
    config = Config(str(ALEMBIC_INI))

    # 실행: 한 단계 내렸다가 다시 올린다
    command.downgrade(config, "-1")
    command.upgrade(config, "head")

    # 확인: 다시 모델과 같다
    command.check(config)
