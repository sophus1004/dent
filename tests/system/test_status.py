"""외부 연결 상태 테스트: 같은 연결은 1분 동안 다시 부르지 않고, 바뀐 연결은 바로 확인한다.
내장 모델은 모델 서버의 단계(내려받는 중 · 불러오는 중 · 종료됨)를 먼저 보이고 기억하지 않는다."""

from dent.system import connections as connections_service
from dent.system import status as status_service
from dent.system.config import get_settings
from dent.system.connections import new_connection
from dent.system.models import ConnectionRole, LlmProvider
from tests.system.helpers import (
    JEV_URL,
    LLM_KEY,
    LLM_URL,
    jev_transport,
    llm_transport,
    model_server_transport,
    refused_transport,
)

# 내려받는 중인 모델: 전체 2GB 가운데 0.5GB
GB = 1024**3


async def test_connection_status_reuses_recent_result_for_same_connection(db_session):
    # 준비
    transport = jev_transport()
    connection = await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )

    # 실행
    first = await status_service.connection_status(db_session, connection, transport=transport)
    second = await status_service.connection_status(db_session, connection, transport=transport)

    # 확인
    assert first is second
    assert len(transport.requests) == 2  # /health와 판정 한 번씩, 한 번만 확인했다


async def test_connection_status_checks_again_when_connection_changes(db_session):
    # 준비
    transport = jev_transport()
    connection = await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )
    await status_service.connection_status(db_session, connection, transport=transport)

    # 실행
    changed = await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model="english"
    )
    await status_service.connection_status(db_session, changed, transport=transport)

    # 확인
    assert len(transport.requests) == 4


async def test_connection_status_checks_again_when_recheck_is_asked(db_session):
    # 준비
    transport = jev_transport()
    connection = await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )
    await status_service.connection_status(db_session, connection, transport=transport)

    # 실행
    await status_service.connection_status(
        db_session, connection, recheck=True, transport=transport
    )

    # 확인
    assert len(transport.requests) == 4


async def test_check_connection_checks_llm_with_model_list(db_session):
    # 준비
    connection = new_connection(
        role=ConnectionRole.LLM,
        base_url=LLM_URL,
        model="test-chat",
        provider=LlmProvider.OPENAI,
        api_key=LLM_KEY,
    )

    # 실행
    result = await status_service.check_connection(
        db_session, connection, transport=llm_transport()
    )

    # 확인
    assert result.ok
    assert result.detail.startswith("test-chat · ")


async def test_readiness_shows_download_progress_of_embedded_model(
    db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(embedding="bge-m3")
    transport = model_server_transport(
        phase="downloading", done_bytes=GB // 2, total_bytes=2 * GB, device=""
    )

    # 실행
    readiness = await status_service.collect_readiness(get_settings(), transport=transport)

    # 확인
    embedding = readiness.models[0]
    assert readiness.info["embedding"].detail == "내려받는 중 · 25% · 0.5 / 2.0GB"
    assert not readiness.info["embedding"].ok
    assert (embedding.role, embedding.embedded, embedding.state) == (
        "embedding",
        "bge-m3",
        "downloading",
    )
    assert (embedding.done_bytes, embedding.total_bytes, embedding.eta_s) == (GB // 2, 2 * GB, 120)
    assert embedding.model == "BAAI/bge-m3"


async def test_readiness_checks_embedded_model_like_server_when_ready(
    db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(jev="laya")
    transport = model_server_transport(model="laya")

    # 실행
    readiness = await status_service.collect_readiness(get_settings(), transport=transport)

    # 확인: 준비되면 판정 하나로 확인한다
    jev = readiness.models[1]
    assert readiness.info["jev"].ok
    assert readiness.info["jev"].detail.startswith(
        "연결됨 · mps · english,multilingual,typed-decisions"
    )
    assert (jev.embedded, jev.state, jev.device) == ("laya", "ok", "mps")


async def test_readiness_shows_embedded_model_server_off(db_session, choose_embedded_models):
    # 준비
    choose_embedded_models(embedding="bge-m3")

    # 실행
    readiness = await status_service.collect_readiness(
        get_settings(), transport=refused_transport()
    )

    # 확인
    assert readiness.info["embedding"].detail == "실패 · 모델 서버 종료됨"
    assert readiness.models[0].state == "failed"


async def test_connection_status_does_not_remember_while_embedded_model_prepares(
    db_session, choose_embedded_models
):
    # 준비: 불러오는 중에 한 번 물었다
    choose_embedded_models(embedding="bge-m3")
    connection = await connections_service.get_connection(db_session, role=ConnectionRole.EMBEDDING)
    assert connection is not None
    await status_service.connection_status(
        db_session, connection, transport=model_server_transport(phase="loading")
    )

    # 실행: 준비된 뒤 다시 묻는다
    result = await status_service.connection_status(
        db_session, connection, transport=model_server_transport()
    )

    # 확인: 1분을 기다리지 않고 바로 연결됨이다
    assert result.ok
    assert result.facts["device"] == "mps"


async def test_readiness_lists_three_models_off_when_nothing_is_connected(db_session):
    # 실행
    readiness = await status_service.collect_readiness(
        get_settings(), transport=refused_transport()
    )

    # 확인
    assert [(model.role, model.state, model.embedded) for model in readiness.models] == [
        ("embedding", "off", None),
        ("jev", "off", None),
        ("llm", "off", None),
    ]
