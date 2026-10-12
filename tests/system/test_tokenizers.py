"""학습할 모델의 토크나이저 테스트: tokenizer.json 하나만 받아 storage/models/tokenizers에 두고, 대화 길이를 토큰으로 센다."""

import httpx
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from dent.system import tokenizers
from dent.system.config import get_settings
from dent.system.exceptions import InvalidInputError, NotFoundError


def _tokenizer_json() -> bytes:
    """낱말마다 토큰 하나인 작은 토크나이저 (모르는 낱말은 [UNK])."""
    vocab = {"[UNK]": 0, "안녕하세요": 1, "반갑습니다": 2}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer.to_str().encode("utf-8")


def _hub(requests: list[httpx.Request]) -> httpx.MockTransport:
    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/acme/tiny/resolve/main/tokenizer.json":
            return httpx.Response(200, content=_tokenizer_json())
        return httpx.Response(404)

    return httpx.MockTransport(reply)


async def test_ensure_tokenizer_downloads_only_tokenizer_json_once():
    # 준비
    requests: list[httpx.Request] = []
    settings = get_settings()

    # 실행
    path = await tokenizers.ensure_tokenizer(settings, "acme/tiny", transport=_hub(requests))
    again = await tokenizers.ensure_tokenizer(settings, "acme/tiny", transport=_hub(requests))

    # 확인: 한 번만 받고 storage/models/tokenizers/acme--tiny/ 아래에 둔다
    assert path == again == settings.embedded_models_path / "tokenizers/acme--tiny/tokenizer.json"
    assert path.exists() and not path.with_suffix(".part").exists()
    assert len(requests) == 1


async def test_ensure_tokenizer_rejects_bad_name_and_reports_missing_repo():
    # 준비
    settings = get_settings()

    # 실행 · 확인
    with pytest.raises(InvalidInputError):
        await tokenizers.ensure_tokenizer(settings, "no-owner", transport=_hub([]))
    with pytest.raises(NotFoundError, match="tokenizer.json"):
        await tokenizers.ensure_tokenizer(settings, "acme/missing", transport=_hub([]))


async def test_count_conversations_adds_message_overhead():
    # 준비
    path = await tokenizers.ensure_tokenizer(get_settings(), "acme/tiny", transport=_hub([]))
    conversations = [
        [
            {"role": "user", "content": "안녕하세요"},
            {"role": "assistant", "content": "반갑습니다 반갑습니다"},
        ],
        [{"role": "user", "content": ""}],
    ]

    # 실행
    counts = tokenizers.count_conversations(path, conversations)

    # 확인: 글 토큰(1 + 2) + 메시지마다 4 · 빈 글은 템플릿 몫만
    overhead = tokenizers.MESSAGE_OVERHEAD_TOKENS
    assert counts == [3 + 2 * overhead, overhead]
