"""분류 모듈 테스트 준비: 테이블 비우기, 데이터셋 만들기."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification.models import Label, Record
from dent.modules.classification.service import MODULE_NAME
from dent.system.models import Dataset
from dent.system.text import make_text_hash
from tests.modules.classification.helpers import MadeDataset, Row


@pytest.fixture(autouse=True)
async def clean_tables(db_session: AsyncSession) -> AsyncIterator[None]:
    """분류 테스트가 끝날 때마다 모든 테이블을 비운다(db_session이 끝나며 비운다)."""
    yield


@pytest.fixture
def make_dataset(db_session: AsyncSession) -> Callable[..., Awaitable[MadeDataset]]:
    """분류 데이터셋(시스템 목록 한 줄)과 라벨, 문장을 DB에 바로 넣는 함수를 돌려준다."""

    async def make(
        name: str, rows: list[Row], *, extra_labels: tuple[str, ...] = ()
    ) -> MadeDataset:
        dataset = Dataset(module=MODULE_NAME, name=name)
        db_session.add(dataset)
        await db_session.flush()
        made = MadeDataset(dataset=dataset)
        for label_name in [*dict.fromkeys(row.label for row in rows), *extra_labels]:
            label = Label(dataset_id=dataset.id, name=label_name)
            db_session.add(label)
            made.labels[label_name] = label
        await db_session.flush()
        for row in rows:
            record = Record(
                dataset_id=dataset.id,
                label_id=made.labels[row.label].id,
                text=row.text,
                text_hash=make_text_hash(row.text),
                exclude_reason=row.exclude_reason,
                trashed_at=datetime.now(UTC) if row.trashed else None,
            )
            db_session.add(record)
            made.records.append(record)
        await db_session.commit()
        return made

    return make
