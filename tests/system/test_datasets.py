"""데이터셋 목록 테스트: 모듈별 이름 규칙, 모듈 거르기, 동시에 들어온 같은 이름."""

import pytest

from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.schemas import DatasetUpdate


async def _name_is_free(*_args: object, **_kwargs: object) -> bool:
    """미리 확인에서 '비어 있다'고 답한다. 두 요청이 동시에 확인을 통과한 경우를 흉내 낸다."""
    return False


async def test_create_dataset_allows_same_name_in_other_module(db_session):
    # 실행
    first = await datasets_service.create_dataset(db_session, module="classification", name="상담")
    second = await datasets_service.create_dataset(db_session, module="generation", name="상담")
    await db_session.commit()

    # 확인
    assert (first.module, second.module) == ("classification", "generation")
    assert first.id != second.id


async def test_create_dataset_fails_when_name_is_taken_in_same_module(db_session):
    # 준비
    await datasets_service.create_dataset(db_session, module="classification", name="상담")
    await db_session.commit()

    # 실행
    with pytest.raises(ConflictError) as caught:
        await datasets_service.create_dataset(db_session, module="classification", name="상담")

    # 확인
    assert caught.value.message == "같은 이름의 데이터셋이 이미 있습니다."


async def test_create_dataset_fails_with_conflict_when_unique_constraint_catches_race(
    db_session, monkeypatch
):
    # 준비
    await datasets_service.create_dataset(db_session, module="classification", name="상담")
    await db_session.commit()
    monkeypatch.setattr(datasets_service, "_name_taken", _name_is_free)

    # 실행
    with pytest.raises(ConflictError):
        await datasets_service.create_dataset(db_session, module="classification", name="상담")


async def test_list_datasets_filters_by_module_and_puts_recent_first(db_session):
    # 준비
    older = await datasets_service.create_dataset(db_session, module="classification", name="가")
    other = await datasets_service.create_dataset(db_session, module="generation", name="나")
    newer = await datasets_service.create_dataset(db_session, module="classification", name="다")
    await db_session.commit()

    # 실행
    everything = await datasets_service.list_datasets(db_session)
    classification = await datasets_service.list_datasets(db_session, module="classification")

    # 확인
    # 같은 트랜잭션에서 만들어 수정 시각이 같으므로 번호가 큰 것이 먼저다.
    assert [dataset.id for dataset in everything] == [newer.id, other.id, older.id]
    assert [dataset.id for dataset in classification] == [newer.id, older.id]


async def test_get_dataset_fails_when_dataset_belongs_to_other_module(db_session):
    # 준비
    dataset = await datasets_service.create_dataset(db_session, module="generation", name="대화")
    await db_session.commit()

    # 실행
    with pytest.raises(NotFoundError) as caught:
        await datasets_service.get_dataset(
            db_session, dataset_id=dataset.id, module="classification"
        )

    # 확인
    assert caught.value.message == "데이터셋을 찾을 수 없습니다."


async def test_update_dataset_renames_within_module_rules(db_session):
    # 준비
    mine = await datasets_service.create_dataset(db_session, module="classification", name="옛")
    await datasets_service.create_dataset(db_session, module="generation", name="새")
    await db_session.commit()

    # 실행: 다른 모듈에 같은 이름이 있어도 된다.
    renamed = await datasets_service.update_dataset(
        db_session, dataset_id=mine.id, data=DatasetUpdate(name="새", description="설명")
    )

    # 확인
    assert (renamed.name, renamed.description) == ("새", "설명")


async def test_touch_dataset_moves_dataset_to_top_of_list(db_session):
    # 준비
    first = await datasets_service.create_dataset(db_session, module="classification", name="가")
    await db_session.commit()
    second = await datasets_service.create_dataset(db_session, module="classification", name="나")
    await db_session.commit()

    # 실행
    await datasets_service.touch_dataset(db_session, dataset_id=first.id)
    await db_session.commit()

    # 확인
    listed = await datasets_service.list_datasets(db_session, module="classification")
    assert [dataset.id for dataset in listed] == [first.id, second.id]
