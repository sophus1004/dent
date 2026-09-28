"""LLM 서버 테스트(웹 없이): 모델 목록으로 연결 확인, 공급자마다 키 헤더, 글 만들기(OpenAI · Anthropic 방식), 키 힌트."""

import json

import httpx
import pytest

from dent.system import llm
from dent.system.exceptions import ExternalServiceError
from dent.system.llm import ChatMessage
from dent.system.models import Connection, LlmProvider
from tests.system.helpers import LLM_KEY, LLM_URL, llm_transport, refused_transport

QUESTION = [ChatMessage(role="system", content="짧게"), ChatMessage(role="user", content="안녕?")]


def _connection(
    *,
    provider: LlmProvider = LlmProvider.OPENAI,
    model: str | None = "test-chat",
    key: str | None = LLM_KEY,
) -> Connection:
    return Connection(
        role="llm", base_url=LLM_URL, model=model, provider=provider.value, api_key=key
    )


async def test_check_llm_lists_models_and_confirms_chosen_model():
    # 실행
    result = await llm.check_llm(_connection(), transport=llm_transport())

    # 확인
    assert result.ok is True
    assert result.detail.startswith("test-chat · ")
    assert result.facts["models"] == ["test-chat", "test-chat-mini"]


async def test_check_llm_lists_models_when_model_is_not_chosen():
    # 실행
    result = await llm.check_llm(_connection(model=None), transport=llm_transport())

    # 확인
    assert result.ok is True
    assert result.detail.startswith("모델 2개 · ")


async def test_check_llm_fails_when_model_is_not_in_list():
    # 실행
    result = await llm.check_llm(_connection(model="no-such-model"), transport=llm_transport())

    # 확인
    assert (result.ok, result.detail) == (False, "모델 없음")
    assert result.facts["models"] == ["test-chat", "test-chat-mini"]


async def test_check_llm_reports_rejected_key():
    # 실행
    result = await llm.check_llm(_connection(key="sk-wrong-key-000000"), transport=llm_transport())

    # 확인
    assert (result.ok, result.detail) == (False, "API 키 거부")


async def test_check_llm_needs_key_except_for_vllm():
    # 실행
    openai = await llm.check_llm(_connection(key=None), transport=llm_transport())
    vllm = await llm.check_llm(
        _connection(provider=LlmProvider.VLLM, key=None), transport=llm_transport(key="")
    )

    # 확인
    assert (openai.ok, openai.detail) == (False, "API 키 필요")
    assert vllm.ok is True


async def test_check_llm_reports_refused_connection():
    # 실행
    result = await llm.check_llm(_connection(), transport=refused_transport())

    # 확인
    assert (result.ok, result.detail) == (False, "연결 거부")


async def test_check_llm_sends_anthropic_key_headers():
    # 준비
    transport = llm_transport()

    # 실행
    await llm.check_llm(_connection(provider=LlmProvider.ANTHROPIC), transport=transport)

    # 확인
    request = transport.requests[0]  # type: ignore[attr-defined]
    assert request.headers["x-api-key"] == LLM_KEY
    assert request.headers["anthropic-version"] == llm.ANTHROPIC_VERSION
    assert "authorization" not in request.headers


async def test_complete_sends_openai_chat_with_completion_token_limit():
    # 준비
    transport = llm_transport(reply="반가워요")

    # 실행
    text = await llm.complete(_connection(), messages=QUESTION, max_tokens=50, transport=transport)

    # 확인
    body = json.loads(transport.requests[0].content)  # type: ignore[attr-defined]
    assert text == "반가워요"
    assert body["model"] == "test-chat"
    assert body["max_completion_tokens"] == 50
    assert body["messages"][0] == {"role": "system", "content": "짧게"}


async def test_complete_uses_max_tokens_for_openai_compatible_servers():
    # 준비
    transport = llm_transport()

    # 실행
    await llm.complete(
        _connection(provider=LlmProvider.XAI), messages=QUESTION, max_tokens=50, transport=transport
    )

    # 확인
    body = json.loads(transport.requests[0].content)  # type: ignore[attr-defined]
    assert body["max_tokens"] == 50
    assert "max_completion_tokens" not in body


async def test_complete_sends_system_separately_to_anthropic():
    # 준비
    transport = llm_transport(reply="네")

    # 실행
    text = await llm.complete(
        _connection(provider=LlmProvider.ANTHROPIC),
        messages=QUESTION,
        max_tokens=50,
        transport=transport,
    )

    # 확인
    body = json.loads(transport.requests[0].content)  # type: ignore[attr-defined]
    assert text == "네"
    assert body["system"] == "짧게"
    assert body["messages"] == [{"role": "user", "content": "안녕?"}]


async def test_complete_raises_external_error_without_key_in_message():
    # 실행 · 확인
    with pytest.raises(ExternalServiceError) as caught:
        await llm.complete(
            _connection(key="sk-wrong-key-000000"),
            messages=QUESTION,
            max_tokens=50,
            transport=llm_transport(),
        )
    assert caught.value.message == "LLM 답을 받지 못했습니다 (API 키 거부)."
    assert "sk-wrong" not in caught.value.message


def test_api_key_hint_shows_only_edges():
    # 실행 · 확인
    assert llm.api_key_hint(LLM_KEY) == "sk-t…cdef"
    assert llm.api_key_hint("short") == "…"
    assert llm.api_key_hint(None) is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ({"type": "insufficient_quota", "code": "credit_balance_exhausted"}, "크레딧 없음"),
        ({"type": "requests", "code": "rate_limit_exceeded"}, "요청 한도 초과"),
    ],
)
async def test_complete_tells_no_credit_from_rate_limit(error, reason):
    # 준비
    transport = httpx.MockTransport(lambda request: httpx.Response(429, json={"error": error}))

    # 실행 · 확인
    with pytest.raises(ExternalServiceError) as caught:
        await llm.complete(_connection(), messages=QUESTION, max_tokens=50, transport=transport)
    assert caught.value.message == f"LLM 답을 받지 못했습니다 ({reason})."


TOOLS = [
    llm.ToolSpec(
        name="exclude_short",
        description="짧은 문장을 뺀다",
        parameters={
            "type": "object",
            "properties": {"say": {"type": "string"}},
            "required": ["say"],
        },
    )
]


async def test_chat_reads_openai_tool_calls_and_sends_tool_results():
    # 준비
    seen: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        message = {
            "role": "assistant",
            "content": "짧은 문장부터?",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "exclude_short", "arguments": '{"say": "뺀다"}'},
                }
            ],
        }
        usage = {"prompt_tokens": 120, "completion_tokens": 30}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})

    history = [
        ChatMessage(role="user", content="진단"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=(llm.ToolCall(id="call_0", name="exclude_short", arguments={"say": "앞"}),),
        ),
        ChatMessage(role="tool", content='{"changed": 5}', tool_call_id="call_0"),
    ]

    # 실행
    result = await llm.chat(
        _connection(),
        messages=history,
        tools=TOOLS,
        max_tokens=100,
        transport=httpx.MockTransport(reply),
    )

    # 확인
    assert result.content == "짧은 문장부터?"
    assert result.tool_calls == [
        llm.ToolCall(id="call_1", name="exclude_short", arguments={"say": "뺀다"})
    ]
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    body = seen[0]
    assert body["tools"][0]["function"]["name"] == "exclude_short"
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"say": "앞"}'
    assert body["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_0",
        "content": '{"changed": 5}',
    }


async def test_chat_reads_anthropic_tool_use_and_groups_tool_results():
    # 준비
    seen: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = [
            {"type": "text", "text": "뺄까?"},
            {"type": "tool_use", "id": "tu_1", "name": "exclude_short", "input": {"say": "뺀다"}},
        ]
        return httpx.Response(
            200, json={"content": content, "usage": {"input_tokens": 90, "output_tokens": 12}}
        )

    calls = (
        llm.ToolCall(id="tu_a", name="exclude_short", arguments={"say": "가"}),
        llm.ToolCall(id="tu_b", name="exclude_short", arguments={"say": "나"}),
    )
    history = [
        ChatMessage(role="system", content="지시"),
        ChatMessage(role="user", content="진단"),
        ChatMessage(role="assistant", content="둘 다", tool_calls=calls),
        ChatMessage(role="tool", content="1", tool_call_id="tu_a"),
        ChatMessage(role="tool", content="2", tool_call_id="tu_b"),
    ]

    # 실행
    result = await llm.chat(
        _connection(provider=LlmProvider.ANTHROPIC),
        messages=history,
        tools=TOOLS,
        max_tokens=100,
        transport=httpx.MockTransport(reply),
    )

    # 확인
    assert result.content == "뺄까?"
    assert result.tool_calls[0].arguments == {"say": "뺀다"}
    assert result.input_tokens == 90
    body = seen[0]
    assert body["system"] == "지시"
    assert body["tools"][0]["input_schema"]["required"] == ["say"]
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "user"]
    assert [block["tool_use_id"] for block in body["messages"][2]["content"]] == ["tu_a", "tu_b"]
