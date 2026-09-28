"""작업 실행기 테스트: 임베딩 연결 확인(DB에서 연결을 읽고, 모델을 등록하고, 차원이 바뀌어도 멈추지 않는다)과
처음 실행할 때 한 번만 하는 내장 예시 넣기."""

from sqlalchemy import delete, func, select

from dent import worker
from dent.modules.classification import examples as classification_examples
from dent.modules.retrieval import examples as retrieval_examples
from dent.system import connections as connections_service
from dent.system import datasets as datasets_service
from dent.system import examples as examples_service
from dent.system.models import ConnectionRole, Dataset, EmbeddingModel, Flag

# 모든 모듈의 내장 예시
ALL_EXAMPLES = (*classification_examples.EXAMPLES, *retrieval_examples.EXAMPLES)
from tests.system.helpers import EMBEDDING_MODEL, EMBEDDING_URL, embedding_transport


async def _save_embedding(db_session, *, model: str = EMBEDDING_MODEL) -> None:
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url=EMBEDDING_URL, model=model
    )


async def test_check_embedding_reports_not_connected_when_nothing_is_saved(db_session):
    # 실행
    state = await worker._check_embedding(quiet=False, transport=embedding_transport())

    # 확인
    assert state.target is None
    assert state.check.detail == "미연결"


async def test_check_embedding_registers_model_from_saved_connection(db_session):
    # 준비
    await _save_embedding(db_session)

    # 실행
    state = await worker._check_embedding(quiet=False, transport=embedding_transport(dim=8))

    # 확인
    models = list(await db_session.scalars(select(EmbeddingModel)))
    assert state.check.ok
    assert state.target == (EMBEDDING_URL, EMBEDDING_MODEL)
    assert [(model.name, model.dim) for model in models] == [(EMBEDDING_MODEL, 8)]


async def test_check_embedding_keeps_running_when_dimension_changes(db_session):
    # 준비
    await _save_embedding(db_session)
    await worker._check_embedding(quiet=False, transport=embedding_transport(dim=8))

    # 실행
    state = await worker._check_embedding(quiet=False, transport=embedding_transport(dim=4))

    # 확인
    assert not state.check.ok
    assert state.check.detail == "차원 불일치 · 등록 8 · 지금 4"


async def test_refresh_embedding_picks_up_saved_connection_without_restart(db_session):
    # 준비
    transport = embedding_transport(dim=8)
    state = await worker._check_embedding(quiet=False, transport=transport)
    await _save_embedding(db_session)

    # 실행
    refreshed = await worker._refresh_embedding(state, transport=transport)
    unchanged = await worker._refresh_embedding(refreshed, transport=transport)

    # 확인
    assert refreshed.check.ok
    assert unchanged is refreshed
    assert len(transport.requests) == 1


async def test_install_first_run_examples_adds_every_example_only_once(db_session):
    # 실행: 처음 실행할 때와 다시 실행할 때
    first = await worker._install_first_run_examples()
    second = await worker._install_first_run_examples()

    # 확인: 처음에만 모든 모듈의 예시를 가져오기로 넣고, 한 번 했다는 표시를 남긴다.
    names = set(await db_session.scalars(select(Dataset.name)))
    assert (first, second) == (len(ALL_EXAMPLES), 0)
    assert names == {example.name for example in ALL_EXAMPLES}
    assert await db_session.get(Flag, examples_service.FIRST_RUN_FLAG) is not None


async def test_install_first_run_examples_does_not_bring_back_deleted_examples(db_session):
    # 준비: 처음 실행해서 넣은 예시를 사람이 모두 지웠다.
    await worker._install_first_run_examples()
    await db_session.execute(delete(Dataset))
    await db_session.commit()

    # 실행
    started = await worker._install_first_run_examples()

    # 확인
    assert started == 0
    assert await db_session.scalar(select(func.count()).select_from(Dataset)) == 0


async def test_install_first_run_examples_leaves_database_in_use_alone(db_session):
    # 준비: 이 기능 전부터 데이터셋이 있던 DB
    await datasets_service.create_dataset(db_session, module="classification", name="쓰던 데이터")
    await db_session.commit()

    # 실행
    started = await worker._install_first_run_examples()

    # 확인: 예시를 넣지 않고 표시만 남긴다(뒤에 데이터셋을 지워도 넣지 않는다).
    names = list(await db_session.scalars(select(Dataset.name)))
    assert started == 0
    assert names == ["쓰던 데이터"]
    assert await db_session.get(Flag, examples_service.FIRST_RUN_FLAG) is not None
