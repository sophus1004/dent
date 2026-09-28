"""외부 모델 연결 API 테스트: 목록, 확인만 하기, 저장 · 확인, 연결 끊기, /readyz가 다시 켜지 않고 바뀌는지,
LLM의 공급자 · API 키(응답에 싣지 않기, 비우면 저장된 키 쓰기)."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import status

from dent.api import connections as connections_api
from dent.api import health as health_api
from dent.main import app
from dent.system import embedding
from tests.system.helpers import (
    EMBEDDING_MODEL,
    EMBEDDING_URL,
    JEV_URL,
    LLM_KEY,
    LLM_URL,
    embedding_transport,
    jev_transport,
    llm_transport,
    refused_transport,
)

API = "/api/v1/connections"


def _fake_servers(request: httpx.Request) -> httpx.Response:
    """주소의 서버 이름으로 가짜 임베딩 · Jev · LLM 서버에 나눠 보낸다. 모르는 서버는 연결 거부."""
    if request.url.host == "embedding.test":
        return embedding_transport(dim=8).handle_request(request)
    if request.url.host == "jev.test":
        return jev_transport().handle_request(request)
    if request.url.host == "llm.test":
        return llm_transport().handle_request(request)
    return refused_transport().handle_request(request)


@pytest.fixture(autouse=True)
def fake_servers() -> Iterator[None]:
    """API와 /readyz가 실제 네트워크 대신 가짜 서버를 부르게 한다."""
    transport = httpx.MockTransport(_fake_servers)
    app.dependency_overrides[connections_api.get_http_transport] = lambda: transport
    app.dependency_overrides[health_api.get_http_transport] = lambda: transport
    yield
    app.dependency_overrides.clear()


async def test_list_connections_returns_three_roles_when_nothing_is_saved(client, db_session):
    # 실행
    response = await client.get(API)

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "role": role,
            "embedded": None,
            "base_url": None,
            "model": None,
            "provider": None,
            "api_key_hint": None,
            "updated_at": None,
            "check": None,
        }
        for role in ("embedding", "jev", "llm")
    ]


async def test_check_connection_tests_without_saving(client, db_session):
    # 실행
    response = await client.post(
        f"{API}/embedding/check", json={"base_url": EMBEDDING_URL, "model": EMBEDDING_MODEL}
    )

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert body["ok"] is True
    assert body["facts"]["dim"] == 8
    listed = (await client.get(API)).json()
    assert listed[0]["base_url"] is None


async def test_check_connection_reports_refused_connection(client, db_session):
    # 실행
    response = await client.post(f"{API}/jev/check", json={"base_url": "http://127.0.0.1:8099"})

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"ok": False, "detail": "연결 거부", "facts": {}}


async def test_save_connection_saves_checks_and_lists(client, db_session):
    # 실행
    response = await client.put(f"{API}/jev", json={"base_url": f"{JEV_URL}/", "model": ""})

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["role"], body["base_url"], body["model"]) == ("jev", JEV_URL, None)
    assert body["check"]["ok"] is True
    assert body["check"]["facts"]["device"] == "mps"
    listed = (await client.get(API)).json()
    assert listed[1]["base_url"] == JEV_URL
    assert listed[1]["check"]["ok"] is True


async def test_save_connection_returns_422_when_address_is_not_http(client, db_session):
    # 실행
    response = await client.put(f"{API}/jev", json={"base_url": "127.0.0.1:8010"})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "http:// 또는 https://" in response.json()["detail"]


async def test_save_connection_returns_422_when_embedding_model_is_missing(client, db_session):
    # 실행
    response = await client.put(f"{API}/embedding", json={"base_url": EMBEDDING_URL})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "모델 이름" in response.json()["detail"]


async def test_save_connection_returns_422_for_unknown_role(client, db_session):
    # 실행
    response = await client.put(f"{API}/search", json={"base_url": JEV_URL})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


LLM = {"base_url": LLM_URL, "provider": "openai", "model": "test-chat"}


async def test_save_llm_connection_checks_and_hides_key(client, db_session):
    # 실행
    response = await client.put(f"{API}/llm", json={**LLM, "api_key": LLM_KEY})

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["provider"], body["model"], body["api_key_hint"]) == (
        "openai",
        "test-chat",
        "sk-t…cdef",
    )
    assert body["check"]["ok"] is True
    assert body["check"]["facts"]["models"] == ["test-chat", "test-chat-mini"]
    assert LLM_KEY not in response.text
    assert LLM_KEY not in (await client.get(API)).text


async def test_save_llm_connection_keeps_saved_key_when_key_is_left_blank(client, db_session):
    # 준비
    await client.put(f"{API}/llm", json={**LLM, "api_key": LLM_KEY})

    # 실행
    response = await client.put(
        f"{API}/llm", json={**LLM, "model": "test-chat-mini", "api_key": ""}
    )

    # 확인
    body = response.json()
    assert body["api_key_hint"] == "sk-t…cdef"
    assert body["check"]["ok"] is True
    assert body["check"]["detail"].startswith("test-chat-mini · ")


async def test_save_llm_connection_drops_saved_key_when_server_changes(client, db_session):
    # 준비
    await client.put(f"{API}/llm", json={**LLM, "api_key": LLM_KEY})

    # 실행: 다른 서버로 바꾸면서 키를 비운다(앞 서버의 키를 새 서버에 보내지 않는다).
    response = await client.put(f"{API}/llm", json={**LLM, "base_url": "http://other.test/v1"})

    # 확인
    body = response.json()
    assert body["api_key_hint"] is None
    assert body["check"] == {"ok": False, "detail": "API 키 필요", "facts": {}}


async def test_check_llm_connection_uses_saved_key_and_lists_models(client, db_session):
    # 준비
    await client.put(f"{API}/llm", json={**LLM, "api_key": LLM_KEY})

    # 실행: 모델을 비우고 확인하면 모델 목록을 받는다.
    response = await client.post(f"{API}/llm/check", json={**LLM, "model": ""})

    # 확인
    body = response.json()
    assert body["ok"] is True
    assert body["detail"].startswith("모델 2개 · ")
    assert body["facts"]["models"] == ["test-chat", "test-chat-mini"]


async def test_check_llm_connection_reports_rejected_key(client, db_session):
    # 실행
    response = await client.post(f"{API}/llm/check", json={**LLM, "api_key": "sk-wrong-key-000000"})

    # 확인
    assert response.json() == {"ok": False, "detail": "API 키 거부", "facts": {}}


async def test_save_llm_connection_returns_422_without_provider_or_model(client, db_session):
    # 실행
    no_provider = await client.put(f"{API}/llm", json={"base_url": LLM_URL, "model": "test-chat"})
    no_model = await client.put(f"{API}/llm", json={**LLM, "model": "", "api_key": LLM_KEY})

    # 확인
    assert no_provider.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert no_provider.json() == {"detail": "LLM 공급자를 골라 주세요."}
    assert no_model.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "모델 이름" in no_model.json()["detail"]


async def test_save_connection_ignores_provider_for_other_roles(client, db_session):
    # 실행
    response = await client.put(f"{API}/jev", json={"base_url": JEV_URL, "provider": "openai"})

    # 확인
    assert response.json()["provider"] is None


async def test_save_embedding_connection_reports_dimension_mismatch(client, db_session):
    # 준비
    await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=1024)

    # 실행
    response = await client.put(
        f"{API}/embedding", json={"base_url": EMBEDDING_URL, "model": EMBEDDING_MODEL}
    )

    # 확인
    assert response.json()["check"]["ok"] is False
    assert response.json()["check"]["detail"] == "차원 불일치 · 등록 1024 · 지금 8"


async def test_readyz_changes_right_after_saving_and_deleting(client, db_session):
    # 준비
    before = (await client.get("/readyz")).json()["info"]

    # 실행
    await client.put(f"{API}/embedding", json={"base_url": EMBEDDING_URL, "model": EMBEDDING_MODEL})
    await client.put(f"{API}/jev", json={"base_url": JEV_URL})
    await client.put(f"{API}/llm", json={**LLM, "api_key": LLM_KEY})
    saved = (await client.get("/readyz")).json()["info"]
    await client.delete(f"{API}/jev")
    deleted = (await client.get("/readyz")).json()["info"]

    # 확인
    assert (before["embedding"]["detail"], before["jev"]["detail"]) == ("미연결", "미연결")
    assert saved["embedding"]["ok"] is True
    assert saved["embedding"]["detail"].startswith("연결됨 · 8차원 · ")
    assert saved["jev"]["detail"].startswith("연결됨 · mps · english,multilingual · ")
    assert saved["llm"]["detail"].startswith("연결됨 · test-chat · ")
    assert deleted["jev"] == {"ok": False, "detail": "미연결"}
    assert deleted["embedding"]["ok"] is True


async def test_readyz_reports_failed_saved_connection(client, db_session):
    # 준비
    await client.put(f"{API}/jev", json={"base_url": "http://127.0.0.1:8099"})

    # 실행
    response = await client.get("/readyz")

    # 확인
    assert response.json()["info"]["jev"] == {"ok": False, "detail": "실패 · 연결 거부"}


async def test_delete_connection_returns_404_when_nothing_is_saved(client, db_session):
    # 실행
    response = await client.delete(f"{API}/jev")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "저장된 연결이 없습니다."}


async def test_delete_connection_returns_204_and_clears_saved_connection(client, db_session):
    # 준비
    await client.put(f"{API}/jev", json={"base_url": JEV_URL})

    # 실행
    response = await client.delete(f"{API}/jev")

    # 확인
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert (await client.get(API)).json()[1]["base_url"] is None


async def test_list_connections_marks_embedded_role(client, db_session, choose_embedded_models):
    # 준비
    choose_embedded_models(embedding="bge-m3")

    # 실행
    response = await client.get(API)

    # 확인
    embedding_row = response.json()[0]
    assert (embedding_row["role"], embedding_row["embedded"]) == ("embedding", "bge-m3")
    assert embedding_row["base_url"] == "http://127.0.0.1:8001/v1"
    assert response.json()[1]["embedded"] is None


async def test_save_connection_returns_409_when_role_uses_embedded_model(
    client, db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(embedding="bge-m3")

    # 실행
    response = await client.put(
        f"{API}/embedding", json={"base_url": EMBEDDING_URL, "model": EMBEDDING_MODEL}
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "내장 모델을 쓰는 역할이라 여기서 바꿀 수 없습니다. "
        "다시 실행할 때 '올리지 않음'을 고르세요."
    }


async def test_delete_connection_returns_409_when_role_uses_embedded_model(
    client, db_session, choose_embedded_models
):
    # 준비
    choose_embedded_models(jev="laya")

    # 실행
    response = await client.delete(f"{API}/jev")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
