"""Jev 판정 서버와 이야기하는 곳. 로컬 laya와 TypeSafe의 Jev가 같은 모양(/v1/systemone)을 쓴다.

  POST {주소}/v1/systemone
    {"model"?, "state": {...}, "questions": {"q": {"type": "choice", "instructions", "criteria"}}}
    → {"model", "answers": {"q": {"type", "choice", "probabilities", "confidence", ...}}, "usage"}
  GET {주소}/health → {"status": "ok", "loaded": [...], "device": "mps"}   (laya에만 있다)

model을 빼면 서버가 고른다(laya는 글자를 보고 english · multilingual · typed-decisions 가운데 고른다).
TypeSafe의 Jev는 나중에 Authorization: Bearer 키를 붙인다. 지금은 키 없이 부른다.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from dent.system.connections import ConnectionCheck, describe_failure
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection

# 판정 하나의 시간 한도(초). 서버가 모델을 처음 불러오면 몇 초 걸릴 수 있다.
DECIDE_TIMEOUT_S = 60.0

# 연결 확인의 시간 한도(초). 화면이 기다리므로 판정보다 짧게 둔다.
CHECK_TIMEOUT_S = 20.0

# 서버에 붙을 때 기다리는 시간(초). 주소가 틀렸을 때 화면이 오래 멈춰 있지 않게 짧게 둔다.
CONNECT_TIMEOUT_S = 3.0

# 1초 = 1000밀리초
MS_PER_S = 1000

# 연결을 확인할 때 묻는 아주 작은 판정: 두 답 가운데 하나 고르기
PROBE_QUESTION = "probe"
PROBE_CRITERIA = {"yes": "The text is a greeting.", "no": "The text is not a greeting."}
PROBE_STATE = {"text": "hello"}
PROBE_QUESTIONS = {
    PROBE_QUESTION: {
        "type": "choice",
        "instructions": "Is the text a greeting?",
        "criteria": PROBE_CRITERIA,
    }
}

# /health가 없는 서버(TypeSafe)가 돌려주는 상태 코드. 이때는 건너뛰고 판정만 본다.
NO_HEALTH_STATUS_CODES = (404, 405)


@dataclass(frozen=True)
class JevAnswer:
    """질문 하나에 대한 판정."""

    # 질문 종류 (예: choice)
    type: str

    # 고른 답 이름 (choice). 없으면 None.
    choice: str | None

    # 답 이름별 확률
    probabilities: dict[str, float]

    # 확신도. 서버가 주지 않으면 None.
    confidence: float | None

    # 서버가 준 원래 답 (다른 칸까지 모두)
    raw: dict[str, Any]


@dataclass(frozen=True)
class JevDecision:
    """판정 한 번의 결과."""

    # 답한 모델. laya처럼 서버가 골랐다면 고른 모델(routing.model), 아니면 서버가 적은 model.
    model: str

    # 질문 이름별 판정
    answers: dict[str, JevAnswer]


async def decide(
    connection: Connection,
    *,
    state: dict[str, Any],
    questions: dict[str, Any],
    model: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> JevDecision:
    """판정을 한 번 받는다. model을 주면 연결의 모델 대신 쓴다. 받지 못하면 ExternalServiceError."""
    timeout = httpx.Timeout(DECIDE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            return await _post_systemone(
                client, connection, state=state, questions=questions, model=model
            )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ExternalServiceError(
            f"Jev 판정을 받지 못했습니다 ({describe_failure(error)})."
        ) from None


async def check_jev_server(
    connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None
) -> ConnectionCheck:
    """/health(있으면)와 작은 판정 하나로 연결을 확인한다.

    facts: device · loaded · latency_ms · model. 예외를 내지 않는다.
    """
    timeout = httpx.Timeout(CHECK_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            health = await _read_health(client, connection)
            server_status = health.get("status", "ok")
            if server_status != "ok":
                return ConnectionCheck(ok=False, detail=f"서버 상태 {server_status}")

            started = time.perf_counter()
            decision = await _post_systemone(
                client, connection, state=PROBE_STATE, questions=PROBE_QUESTIONS
            )
            latency_ms = round((time.perf_counter() - started) * MS_PER_S)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        return ConnectionCheck(ok=False, detail=describe_failure(error))

    answer = decision.answers.get(PROBE_QUESTION)
    is_valid_answer = answer is not None and answer.choice in PROBE_CRITERIA
    if not is_valid_answer:
        return ConnectionCheck(ok=False, detail="응답 모양 다름")

    # /health가 없는 서버는 장치와 불러 둔 모델을 모른다. 그 칸은 비운다.
    raw_device = health.get("device")
    device = raw_device if isinstance(raw_device, str) else None
    raw_loaded = health.get("loaded")
    # 서버가 주는 순서는 부를 때마다 달라서 이름 순으로 맞춘다(화면이 흔들리지 않게).
    loaded = sorted(str(name) for name in raw_loaded) if isinstance(raw_loaded, list) else []
    parts = [device, ",".join(loaded), f"{latency_ms}ms"]
    return ConnectionCheck(
        ok=True,
        detail=" · ".join(part for part in parts if part),
        facts={
            "device": device,
            "loaded": loaded,
            "latency_ms": latency_ms,
            "model": decision.model,
        },
    )


async def _read_health(client: httpx.AsyncClient, connection: Connection) -> dict[str, Any]:
    """GET /health의 답. 이 주소가 없는 서버(TypeSafe)면 빈 dict."""
    response = await client.get(f"{connection.base_url}/health")
    if response.status_code in NO_HEALTH_STATUS_CODES:
        return {}
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("health 답이 JSON 객체가 아닙니다.")
    return body


async def _post_systemone(
    client: httpx.AsyncClient,
    connection: Connection,
    *,
    state: dict[str, Any],
    questions: dict[str, Any],
    model: str | None = None,
) -> JevDecision:
    """/v1/systemone에 판정을 묻고 답을 읽는다. 모양이 다르면 KeyError · TypeError · ValueError."""
    payload: dict[str, Any] = {"state": state, "questions": questions}
    chosen_model = model or connection.model
    if chosen_model:
        payload["model"] = chosen_model
    response = await client.post(f"{connection.base_url}/v1/systemone", json=payload)
    response.raise_for_status()
    return _parse_decision(response.json())


def _parse_decision(body: Any) -> JevDecision:
    """/v1/systemone의 답을 JevDecision으로. 모양이 다르면 KeyError · TypeError · ValueError."""
    if not isinstance(body, dict) or not isinstance(body["answers"], dict):
        raise TypeError("판정 답의 모양이 다릅니다.")
    routing = body.get("routing")
    routed_model = routing.get("model") if isinstance(routing, dict) else None
    answers = {name: _parse_answer(raw) for name, raw in body["answers"].items()}
    return JevDecision(model=str(routed_model or body["model"]), answers=answers)


def _parse_answer(raw: Any) -> JevAnswer:
    """답 하나를 JevAnswer로. 모양이 다르면 KeyError · TypeError · ValueError."""
    if not isinstance(raw, dict):
        raise TypeError("판정 답의 모양이 다릅니다.")
    choice = raw.get("choice")
    probabilities = raw.get("probabilities") or {}
    confidence = raw.get("confidence")
    return JevAnswer(
        type=str(raw["type"]),
        choice=str(choice) if choice is not None else None,
        probabilities={str(name): float(value) for name, value in probabilities.items()},
        confidence=float(confidence) if confidence is not None else None,
        raw=raw,
    )
