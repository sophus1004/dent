"""외부 모델 연결 저장 테스트: 주소 검사, 저장 · 바꾸기 · 지우기, 임베딩 모델 이름, 내장 모델이 이기는 역할."""

import pytest

from dent.system import connections as connections_service
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import ConnectionRole


def test_normalize_base_url_strips_trailing_slash():
    # 실행
    url = connections_service.normalize_base_url("  http://127.0.0.1:8010/  ")

    # 확인
    assert url == "http://127.0.0.1:8010"


@pytest.mark.parametrize("value", ["127.0.0.1:8010", "ftp://server", "http://", "file:///tmp"])
def test_normalize_base_url_rejects_non_web_address(value):
    # 실행
    with pytest.raises(InvalidInputError) as caught:
        connections_service.normalize_base_url(value)

    # 확인
    assert "http:// 또는 https://" in caught.value.message


def test_new_connection_requires_model_for_embedding():
    # 실행
    with pytest.raises(InvalidInputError) as caught:
        connections_service.new_connection(
            role=ConnectionRole.EMBEDDING, base_url="http://127.0.0.1:12434/v1", model="  "
        )

    # 확인
    assert "모델 이름" in caught.value.message


def test_new_connection_keeps_empty_jev_model_as_auto():
    # 실행
    connection = connections_service.new_connection(
        role=ConnectionRole.JEV, base_url="http://127.0.0.1:8010", model=""
    )

    # 확인
    assert connection.model is None


async def test_get_connection_returns_none_when_not_saved(db_session):
    # 실행
    connection = await connections_service.get_connection(db_session, role=ConnectionRole.JEV)

    # 확인
    assert connection is None


async def test_save_connection_creates_then_updates_one_row_per_role(db_session):
    # 준비
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url="http://127.0.0.1:8010/", model=None
    )

    # 실행
    saved = await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url="https://jev.example", model="english"
    )

    # 확인
    rows = await connections_service.list_connections(db_session)
    assert (saved.base_url, saved.model) == ("https://jev.example", "english")
    assert [(row.role, row.base_url) for row in rows] == [("jev", "https://jev.example")]


async def test_delete_connection_removes_saved_connection(db_session):
    # 준비
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url="http://127.0.0.1:8010", model=None
    )

    # 실행
    await connections_service.delete_connection(db_session, role=ConnectionRole.JEV)

    # 확인
    assert await connections_service.get_connection(db_session, role=ConnectionRole.JEV) is None


async def test_delete_connection_fails_when_nothing_is_saved(db_session):
    # 실행
    with pytest.raises(NotFoundError):
        await connections_service.delete_connection(db_session, role=ConnectionRole.LLM)


async def test_get_connection_returns_embedded_server_over_saved_row(
    db_session, choose_embedded_models
):
    # 준비: 외부 임베딩 서버를 저장해 둔 뒤 실행할 때 내장 모델을 골랐다
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url="http://127.0.0.1:8020/v1", model="m"
    )
    choose_embedded_models(embedding="bge-m3")

    # 실행
    connection = await connections_service.get_connection(db_session, role=ConnectionRole.EMBEDDING)

    # 확인
    assert connection is not None
    assert (connection.base_url, connection.model) == ("http://127.0.0.1:8001/v1", "BAAI/bge-m3")


async def test_get_connection_returns_saved_row_again_when_embedded_is_cleared(
    db_session, choose_embedded_models
):
    # 준비
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url="http://127.0.0.1:8020/v1", model="m"
    )
    choose_embedded_models(embedding="bge-m3")
    choose_embedded_models(embedding="")

    # 실행
    connection = await connections_service.get_connection(db_session, role=ConnectionRole.EMBEDDING)

    # 확인: 저장된 줄은 지우지 않아 다시 쓰인다
    assert connection is not None
    assert connection.base_url == "http://127.0.0.1:8020/v1"


async def test_list_connections_puts_embedded_servers_in_place_of_saved_rows(
    db_session, choose_embedded_models
):
    # 준비
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url="http://127.0.0.1:8010", model=None
    )
    choose_embedded_models(embedding="bge-m3", jev="laya")

    # 실행
    rows = await connections_service.list_connections(db_session)

    # 확인
    assert [(row.role, row.base_url) for row in rows] == [
        ("embedding", "http://127.0.0.1:8001/v1"),
        ("jev", "http://127.0.0.1:8002"),
    ]


async def test_save_connection_fails_when_role_uses_embedded_model(
    db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(jev="laya")

    # 실행
    with pytest.raises(ConflictError) as caught:
        await connections_service.save_connection(
            db_session, role=ConnectionRole.JEV, base_url="http://127.0.0.1:8010", model=None
        )

    # 확인
    assert "올리지 않음" in caught.value.message


async def test_delete_connection_fails_when_role_uses_embedded_model(
    db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(embedding="bge-m3")

    # 실행
    with pytest.raises(ConflictError):
        await connections_service.delete_connection(db_session, role=ConnectionRole.EMBEDDING)
