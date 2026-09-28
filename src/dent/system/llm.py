"""LLM 서버와 이야기하는 곳. 공급자(OpenAI · vLLM · Anthropic · Google · xAI)마다 다른 부르는 방식을 여기에 모은다.

  연결 확인   GET {주소}/models → {"data": [{"id": ...}, ...]}   (토큰을 쓰지 않는다)
              모델을 정했으면 목록에 있는지도 본다.
  글 만들기   OpenAI 방식  POST {주소}/chat/completions   (OpenAI · vLLM · xAI · Google의 OpenAI 호환 주소)
              Anthropic    POST {주소}/messages           (system은 따로, max_tokens 필수)
  도구 쓰기   chat()에 도구(ToolSpec)를 주면 LLM이 도구를 부른다(ToolCall). 결과는 tool 말로 돌려준다.
              OpenAI는 tools · tool_calls · role=tool, Anthropic은 tools · tool_use · tool_result 조각.
  키          OpenAI 방식은 Authorization: Bearer, Anthropic은 x-api-key와 anthropic-version 헤더.
              vLLM은 키 없이도 된다.
API 키는 헤더에만 싣는다. 확인 결과 · 로그 · 오류 문장에는 남기지 않는다(화면에는 api_key_hint만).
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from dent.system.connections import ConnectionCheck, describe_failure
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, LlmProvider

# 연결 확인의 시간 한도(초). 화면이 기다린다.
CHECK_TIMEOUT_S = 15.0

# 글 만들기의 시간 한도(초). 긴 답은 수십 초 걸릴 수 있다.
COMPLETE_TIMEOUT_S = 120.0

# 서버에 붙을 때 기다리는 시간(초). 주소가 틀렸을 때 오래 멈춰 있지 않게 짧게 둔다.
CONNECT_TIMEOUT_S = 5.0

# Anthropic API가 요구하는 판(헤더 anthropic-version)
ANTHROPIC_VERSION = "2023-06-01"

# 1초 = 1000밀리초
MS_PER_S = 1000

# 키 힌트: 앞뒤로 보일 글자 수. 이보다 짧은 키는 아무것도 보이지 않는다(앞뒤가 겹쳐 키가 드러나므로).
KEY_HINT_EDGE = 4
KEY_HINT_MIN_LENGTH = KEY_HINT_EDGE * 3

# 키를 거부할 때의 HTTP 상태
KEY_REJECTED_STATUSES = (401, 403)

# 요청 한도 · 크레딧이 모자랄 때의 HTTP 상태. 오류 본문의 code로 둘을 가른다.
TOO_MANY_REQUESTS = 429
QUOTA_ERROR_CODES = ("insufficient_quota", "credit_balance_exhausted")

# 확인 결과의 까닭 낱말
KEY_REQUIRED = "API 키 필요"
KEY_REJECTED = "API 키 거부"
NO_CREDIT = "크레딧 없음"
RATE_LIMITED = "요청 한도 초과"
MODEL_MISSING = "모델 없음"

# 키 없이 부를 수 있는 공급자 (자기 서버)
KEYLESS_PROVIDERS = (LlmProvider.VLLM,)

# max_tokens 대신 max_completion_tokens를 받는 공급자 (OpenAI의 새 모델은 max_tokens를 거부한다)
COMPLETION_TOKENS_PROVIDERS = (LlmProvider.OPENAI,)


@dataclass(frozen=True)
class ToolCall:
    """LLM이 부른 도구 하나."""

    # 공급자가 준 부르기 번호. 도구 결과를 돌려줄 때 같은 번호를 단다.
    id: str

    # 도구 이름
    name: str

    # 인자. JSON으로 읽지 못했으면 빈 사전
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    """대화의 말 하나. 도구를 쓰는 대화면 LLM이 부른 도구와 그 결과도 말로 쌓는다."""

    # system(지시) · user(사람) · assistant(LLM) · tool(도구 결과)
    role: Literal["system", "user", "assistant", "tool"]

    # 말 (도구 결과면 결과 글)
    content: str

    # assistant가 부른 도구들
    tool_calls: tuple[ToolCall, ...] = ()

    # tool 말이면 어느 부르기의 결과인지
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """LLM에 알려 줄 도구 하나."""

    # 도구 이름 (영문 snake_case)
    name: str

    # 무엇을 하는지 (LLM이 읽는다)
    description: str

    # 인자 모양 (JSON Schema의 object)
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ChatReply:
    """LLM의 답 한 번."""

    # 글 (없으면 빈 글자)
    content: str

    # 부른 도구들 (없으면 빈 목록)
    tool_calls: list[ToolCall]

    # 쓴 토큰 (공급자가 알려 준 값, 모르면 0)
    input_tokens: int
    output_tokens: int


def api_key_hint(api_key: str | None) -> str | None:
    """화면에 보일 키의 앞뒤 몇 글자. 예: 'sk-p…rCoA'. 키가 없으면 None, 너무 짧으면 '…'."""
    if not api_key:
        return None
    if len(api_key) < KEY_HINT_MIN_LENGTH:
        return "…"
    return f"{api_key[:KEY_HINT_EDGE]}…{api_key[-KEY_HINT_EDGE:]}"


def provider_of(connection: Connection) -> LlmProvider:
    """연결의 공급자. 적혀 있지 않으면 OpenAI 방식으로 본다."""
    return LlmProvider(connection.provider) if connection.provider else LlmProvider.OPENAI


async def check_llm(
    connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None
) -> ConnectionCheck:
    """모델 목록으로 연결을 확인한다. 모델을 정했으면 목록에 있어야 한다. 예외를 내지 않는다.

    facts: models(이름 순) · latency_ms · model
    """
    provider = provider_of(connection)
    needs_key = provider not in KEYLESS_PROVIDERS
    if needs_key and not connection.api_key:
        return ConnectionCheck(ok=False, detail=KEY_REQUIRED)
    timeout = httpx.Timeout(CHECK_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"{connection.base_url}/models", headers=_headers(connection)
            )
            response.raise_for_status()
            body = response.json()
        models = sorted(str(item["id"]) for item in body["data"])
    except httpx.HTTPStatusError as error:
        return ConnectionCheck(ok=False, detail=_status_reason(error))
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        return ConnectionCheck(ok=False, detail=describe_failure(error))
    latency_ms = round((time.perf_counter() - started) * MS_PER_S)

    facts: dict[str, Any] = {"models": models, "latency_ms": latency_ms, "model": connection.model}
    if connection.model is None:
        return ConnectionCheck(
            ok=True, detail=f"모델 {len(models)}개 · {latency_ms}ms", facts=facts
        )
    if not _has_model(models, connection.model):
        return ConnectionCheck(ok=False, detail=MODEL_MISSING, facts=facts)
    return ConnectionCheck(ok=True, detail=f"{connection.model} · {latency_ms}ms", facts=facts)


async def complete(
    connection: Connection,
    *,
    messages: list[ChatMessage],
    max_tokens: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """대화를 보내고 LLM의 답(글)을 받는다. 모델이 없거나 받지 못하면 ExternalServiceError."""
    reply = await chat(
        connection, messages=messages, tools=[], max_tokens=max_tokens, transport=transport
    )
    return reply.content


async def chat(
    connection: Connection,
    *,
    messages: list[ChatMessage],
    tools: list[ToolSpec],
    max_tokens: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatReply:
    """도구를 줄 수 있는 대화 한 번. 답의 글 · 부른 도구 · 쓴 토큰을 돌려준다.

    모델이 없거나 받지 못하면 ExternalServiceError.
    """
    if connection.model is None:
        raise ExternalServiceError(
            "LLM 모델이 정해지지 않았습니다. 연결 설정에서 모델을 골라 주세요."
        )
    provider = provider_of(connection)
    timeout = httpx.Timeout(COMPLETE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            if provider is LlmProvider.ANTHROPIC:
                return await _chat_anthropic(
                    client, connection, messages=messages, tools=tools, max_tokens=max_tokens
                )
            return await _chat_openai(
                client, connection, messages=messages, tools=tools, max_tokens=max_tokens
            )
    except httpx.HTTPStatusError as error:
        raise ExternalServiceError(f"LLM 답을 받지 못했습니다 ({_status_reason(error)}).") from None
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as error:
        raise ExternalServiceError(
            f"LLM 답을 받지 못했습니다 ({describe_failure(error)})."
        ) from None


async def _chat_openai(
    client: httpx.AsyncClient,
    connection: Connection,
    *,
    messages: list[ChatMessage],
    tools: list[ToolSpec],
    max_tokens: int,
) -> ChatReply:
    """OpenAI 방식(chat/completions)으로 부른다. 첫 선택지의 글과 도구 부르기를 읽는다."""
    uses_completion_tokens = provider_of(connection) in COMPLETION_TOKENS_PROVIDERS
    token_field = "max_completion_tokens" if uses_completion_tokens else "max_tokens"
    body: dict[str, Any] = {
        "model": connection.model,
        "messages": [_openai_message(message) for message in messages],
        token_field: max_tokens,
    }
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
    response = await client.post(
        f"{connection.base_url}/chat/completions", headers=_headers(connection), json=body
    )
    response.raise_for_status()
    data = response.json()
    message = data["choices"][0]["message"]
    calls = [
        ToolCall(
            id=str(call["id"]),
            name=str(call["function"]["name"]),
            arguments=_read_arguments(call["function"].get("arguments")),
        )
        for call in message.get("tool_calls") or []
    ]
    usage = data.get("usage") or {}
    return ChatReply(
        content=str(message.get("content") or ""),
        tool_calls=calls,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )


def _openai_message(message: ChatMessage) -> dict[str, Any]:
    """말 하나를 OpenAI 모양으로. 도구 부르기의 인자는 JSON 글자로 싣는다."""
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    converted: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        converted["content"] = message.content or None
        converted["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return converted


async def _chat_anthropic(
    client: httpx.AsyncClient,
    connection: Connection,
    *,
    messages: list[ChatMessage],
    tools: list[ToolSpec],
    max_tokens: int,
) -> ChatReply:
    """Anthropic 방식(messages)으로 부른다. system은 따로, 도구 결과는 user 말의 tool_result 조각이다."""
    system = "\n\n".join(message.content for message in messages if message.role == "system")
    body: dict[str, Any] = {
        "model": connection.model,
        "max_tokens": max_tokens,
        "messages": _anthropic_messages(messages),
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
            for tool in tools
        ]
    response = await client.post(
        f"{connection.base_url}/messages", headers=_headers(connection), json=body
    )
    response.raise_for_status()
    data = response.json()
    blocks = data["content"]
    usage = data.get("usage") or {}
    return ChatReply(
        content="".join(str(block["text"]) for block in blocks if block.get("type") == "text"),
        tool_calls=[
            ToolCall(
                id=str(block["id"]), name=str(block["name"]), arguments=block.get("input") or {}
            )
            for block in blocks
            if block.get("type") == "tool_use"
        ],
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


def _anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """말들을 Anthropic 모양으로. 잇따른 도구 결과는 user 말 하나로 묶는다(Anthropic이 요구한다)."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            is_after_results = (
                converted
                and converted[-1]["role"] == "user"
                and isinstance(converted[-1]["content"], list)
            )
            if is_after_results:
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": message.role, "content": message.content})
    return converted


def _read_arguments(raw: Any) -> dict[str, Any]:
    """도구 인자를 사전으로. JSON 글자가 아니거나 사전이 아니면 빈 사전."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _status_reason(error: httpx.HTTPStatusError) -> str:
    """실패한 응답의 까닭 낱말: 키 거부 · 크레딧 없음 · 요청 한도 초과, 그 밖은 'HTTP 500' 같은 상태."""
    status_code = error.response.status_code
    if status_code in KEY_REJECTED_STATUSES:
        return KEY_REJECTED
    if status_code == TOO_MANY_REQUESTS:
        return NO_CREDIT if _error_code(error.response) in QUOTA_ERROR_CODES else RATE_LIMITED
    return describe_failure(error)


def _error_code(response: httpx.Response) -> str | None:
    """OpenAI 모양 오류 본문({"error": {"code", "type"}})의 code(없으면 type). 읽지 못하면 None."""
    try:
        detail = response.json().get("error") or {}
    except ValueError:
        return None
    if not isinstance(detail, dict):
        return None
    code = detail.get("code") or detail.get("type")
    return str(code) if code else None


def _headers(connection: Connection) -> dict[str, str]:
    """공급자에 맞는 키 헤더. 키가 없으면 빈 헤더."""
    if not connection.api_key:
        return {}
    if provider_of(connection) is LlmProvider.ANTHROPIC:
        return {"x-api-key": connection.api_key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {connection.api_key}"}


def _has_model(models: list[str], model: str) -> bool:
    """목록에 모델이 있는지. Google은 이름 앞에 'models/'를 붙여 주므로 뒤 이름만 견줘도 된다."""
    return any(name == model or name.endswith(f"/{model}") for name in models)
