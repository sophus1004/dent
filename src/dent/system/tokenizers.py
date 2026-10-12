"""학습할 모델의 토크나이저로 글 길이를 토큰으로 센다(모든 모듈).

사람이 데이터셋 설정에서 학습할 모델(허깅페이스 저장소 이름)을 고르면, 그 저장소의 tokenizer.json 하나(수 MB)만
storage/models/tokenizers/<소유자--이름>/에 받는다(.part에 받고 다 받으면 옮긴다). 비공개 · 동의가 필요한 모델은
.env의 HF_TOKEN을 쓴다. 토크나이저가 없으면 모듈은 글자 수로 어림한다.
대화의 토큰 수는 메시지 글의 토큰에 메시지마다 챗 템플릿이 더하는 몇 토큰(MESSAGE_OVERHEAD_TOKENS)을 더한 어림이다
(모델마다 템플릿이 달라 정확히 같지는 않다).
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from tokenizers import Tokenizer

from dent.system import huggingface
from dent.system.config import Settings
from dent.system.exceptions import InvalidInputError, NotFoundError

# storage/models 안의 토크나이저 폴더 이름과 받는 파일
TOKENIZERS_DIR_NAME = "tokenizers"
TOKENIZER_FILE = "tokenizer.json"

# 허깅페이스 모델 주소의 파일 주소 (저장소 · 파일)
RESOLVE_URL = "https://huggingface.co/{repo}/resolve/main/{file}"

# 모델 이름: 소유자/이름 (글자 · 숫자 · . _ -)
MODEL_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")

# 메시지마다 챗 템플릿이 더하는 토큰 어림 (역할 표지 · 끝 표지)
MESSAGE_OVERHEAD_TOKENS = 4

BAD_NAME_MESSAGE = "모델 이름은 '소유자/이름' 모양입니다(예: Qwen/Qwen3-8B)."


def tokenizer_path(settings: Settings, model: str) -> Path:
    """모델의 토크나이저 파일 자리 (storage/models/tokenizers/소유자--이름/tokenizer.json)."""
    folder = model.replace("/", "--")
    return settings.embedded_models_path / TOKENIZERS_DIR_NAME / folder / TOKENIZER_FILE


def check_model_name(model: str) -> None:
    """모델 이름 모양을 본다. 틀리면 InvalidInputError."""
    if not MODEL_NAME.match(model):
        raise InvalidInputError(BAD_NAME_MESSAGE)


async def ensure_tokenizer(
    settings: Settings, model: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> Path:
    """모델의 토크나이저 파일이 없으면 받는다. 파일 자리를 돌려준다.

    이름이 틀리면 InvalidInputError, 저장소나 파일이 없으면 NotFoundError, 받지 못하면 ExternalServiceError.
    """
    check_model_name(model)
    path = tokenizer_path(settings, model)
    if path.exists():
        return path
    partial = path.with_suffix(".part")
    try:
        await huggingface.download(
            url=RESOLVE_URL.format(repo=model, file=TOKENIZER_FILE),
            target=partial,
            repo=model,
            transport=transport,
        )
    except NotFoundError:
        partial.unlink(missing_ok=True)
        raise NotFoundError(
            f"{model}의 {TOKENIZER_FILE}을 찾지 못했습니다. 이름을 확인하거나, 동의가 필요한 모델이면 "
            ".env의 HF_TOKEN을 넣으세요."
        ) from None
    partial.replace(path)
    load.cache_clear()
    return path


@lru_cache(maxsize=4)
def load(path: Path) -> Tokenizer:
    """토크나이저를 읽는다(파일마다 한 번)."""
    return Tokenizer.from_file(str(path))


def count_conversations(path: Path, conversations: list[list[dict[str, Any]]]) -> list[int]:
    """대화마다 토큰 수 어림: 메시지 글의 토큰 + 메시지마다 MESSAGE_OVERHEAD_TOKENS. 도구 호출은 그 JSON 글을 센다."""
    tokenizer = load(path)
    texts: list[str] = []
    owners: list[int] = []
    for index, messages in enumerate(conversations):
        for message in messages:
            texts.append(str(message.get("content") or ""))
            owners.append(index)
            if message.get("tool_calls"):
                texts.append(str(message["tool_calls"]))
                owners.append(index)
    counts = [len(messages) * MESSAGE_OVERHEAD_TOKENS for messages in conversations]
    for owner, encoded in zip(
        owners, tokenizer.encode_batch(texts, add_special_tokens=False), strict=True
    ):
        counts[owner] += len(encoded.ids)
    return counts
