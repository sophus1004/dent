"""검색 문서 · 청크의 토큰 수를 세는 기준: 학습할 모델의 토크나이저가 이 PC에 있으면 그것으로, 없으면 글자 수로 어림한다.

긴 문서(인코더의 최대 길이를 넘는지)와 문서를 나눌 청크 크기를 이 수로 정한다. 글자 수 어림은 bge-m3 토크나이저보다
10~17% 적게 세어, 최대 길이에 맞춰 나눈 청크가 실제로는 넘쳐 학습할 때 뒤가 잘리고 긴 문서를 놓치곤 했다.
토크나이저는 이렇게 찾는다(여기서는 받지 않는다):
  1. 내장 임베딩 모델을 올렸고 이 데이터셋이 그 모델을 쓰면 그 모델 폴더의 tokenizer.json
  2. 학습할 모델 이름(없으면 연결된 임베딩 모델 이름)으로 받아 둔 tokenizer.json(storage/models/tokenizers)
받기는 사람이 학습할 모델을 바꿨을 때 작업 ("retrieval", "length")이 한다(repeats.recount_dataset).
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.text_rules import estimate_tokens
from dent.system import connections, embedded_models, tokenizers
from dent.system.config import get_settings
from dent.system.models import ConnectionRole

# 글 하나의 토큰 수를 세는 함수
TokenCounter = Callable[[str], int]


@dataclass(frozen=True)
class Counting:
    """토큰을 세는 기준."""

    # 토크나이저의 모델 이름. 글자 수 어림이면 None
    model: str | None

    # 글 하나의 토큰 수를 세는 함수
    count: TokenCounter


# 토크나이저가 없을 때의 기준
ESTIMATE = Counting(model=None, count=estimate_tokens)


async def model_of(db: AsyncSession, *, dataset_id: int) -> str | None:
    """이 데이터셋이 학습할 모델 이름: 설정의 학습할 모델, 없으면 연결된 임베딩 모델."""
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    if settings is not None and settings.target_model:
        return settings.target_model
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    return connection.model if connection is not None else None


def local_tokenizer(model: str | None) -> tuple[str, Path] | None:
    """이 PC에 있는 모델의 토크나이저 (모델 이름, 파일). 없으면 None. 받지 않는다."""
    choice = embedded_models.chosen_choice(ConnectionRole.EMBEDDING)
    if choice is not None and model in (None, choice.model.served_name, choice.model.repo):
        folder = embedded_models.model_folder(get_settings(), choice)
        nested = sorted(folder.glob(f"*/{tokenizers.TOKENIZER_FILE}"))
        for path in (folder / tokenizers.TOKENIZER_FILE, *nested):
            if path.exists():
                return choice.model.served_name or choice.model.repo, path
    if model and tokenizers.MODEL_NAME.match(model):
        path = tokenizers.tokenizer_path(get_settings(), model)
        if path.exists():
            return model, path
    return None


async def counting(db: AsyncSession, *, dataset_id: int) -> Counting:
    """이 데이터셋의 토큰을 세는 기준 (토크나이저가 없으면 글자 수 어림)."""
    found = local_tokenizer(await model_of(db, dataset_id=dataset_id))
    if found is None:
        return ESTIMATE
    model, path = found
    return Counting(model=model, count=partial(tokenizers.count_text, path))
