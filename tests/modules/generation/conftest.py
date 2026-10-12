"""생성 모듈 테스트 준비: 테이블 비우기."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
async def clean_tables(db_session: AsyncSession) -> AsyncIterator[None]:
    """생성 테스트가 끝날 때마다 모든 테이블을 비운다(db_session이 끝나며 비운다)."""
    yield
