"""Jev 판정 서버 테스트: 연결 확인(/health 있음 · 없음), 판정 받기, 모델 고르기. 가짜 서버를 쓴다."""

import json

import pytest

from dent.system import jev
from dent.system.connections import new_connection
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, ConnectionRole
from tests.system.helpers import JEV_URL, jev_transport, refused_transport

QUESTIONS = {
    "q": {
        "type": "choice",
        "instructions": "인사인가?",
        "criteria": {"yes": "인사다", "no": "인사가 아니다"},
    }
}


def _connection(model: str | None = None) -> Connection:
    return new_connection(role=ConnectionRole.JEV, base_url=JEV_URL, model=model)


async def test_check_jev_server_reads_health_and_test_decision():
    # 준비
    transport = jev_transport()

    # 실행
    result = await jev.check_jev_server(_connection(), transport=transport)

    # 확인
    assert result.ok
    assert result.facts["device"] == "mps"
    assert result.facts["loaded"] == ["english", "multilingual"]
    assert result.facts["model"] == "english"
    assert isinstance(result.facts["latency_ms"], int)
    assert result.detail.startswith("mps · english,multilingual · ")
    assert [request.url.path for request in transport.requests] == ["/health", "/v1/systemone"]


async def test_check_jev_server_works_without_health_like_typesafe():
    # 실행
    result = await jev.check_jev_server(_connection(), transport=jev_transport(with_health=False))

    # 확인
    assert result.ok
    assert result.facts["device"] is None
    assert result.facts["loaded"] == []
    assert result.detail.endswith("ms")


async def test_check_jev_server_reports_refused_connection():
    # 실행
    result = await jev.check_jev_server(_connection(), transport=refused_transport())

    # 확인
    assert not result.ok
    assert result.detail == "연결 거부"


async def test_check_jev_server_fails_when_answer_is_not_one_of_the_options():
    # 실행
    result = await jev.check_jev_server(_connection(), transport=jev_transport(choice="maybe"))

    # 확인
    assert not result.ok
    assert result.detail == "응답 모양 다름"


async def test_decide_returns_parsed_answers_and_omits_model_when_auto():
    # 준비
    transport = jev_transport()

    # 실행
    decision = await jev.decide(
        _connection(), state={"text": "안녕"}, questions=QUESTIONS, transport=transport
    )

    # 확인
    answer = decision.answers["q"]
    assert (answer.type, answer.choice, answer.confidence) == ("choice", "yes", 0.7)
    assert answer.probabilities == {"yes": 0.9, "no": 0.1}
    assert decision.model == "english"
    sent = json.loads(transport.requests[0].content)
    assert "model" not in sent
    assert sent["state"] == {"text": "안녕"}


async def test_decide_sends_connection_model_unless_overridden():
    # 준비
    transport = jev_transport()
    connection = _connection(model="multilingual")

    # 실행
    await jev.decide(connection, state={}, questions=QUESTIONS, transport=transport)
    await jev.decide(
        connection, state={}, questions=QUESTIONS, model="english", transport=transport
    )

    # 확인
    sent_models = [json.loads(request.content)["model"] for request in transport.requests]
    assert sent_models == ["multilingual", "english"]


async def test_decide_raises_external_service_error_when_server_is_down():
    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        await jev.decide(
            _connection(), state={}, questions=QUESTIONS, transport=refused_transport()
        )

    # 확인
    assert "연결 거부" in caught.value.message
