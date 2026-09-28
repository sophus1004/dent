"""외부 임베딩 서버와 이야기하는 곳, 그리고 임베딩 캐시(system_embeddings).

서버 형식은 OpenAI 호환(POST {주소}/embeddings, 본문 {"model", "input"})을 가정한다.
연결 값(주소 · 모델)은 system_connections에서 온다.

- 연결 확인과 모델 등록: check_embedding · register_model · ensure_model
- 계산: embed_texts가 문장을 EMBEDDING_BATCH_SIZE개씩 나눠 최대 EMBEDDING_MAX_CONCURRENCY개를 동시에 보낸다.
  잠깐 끊기거나 서버가 바쁘면(5xx · 429 · 시간 초과) 조금씩 더 기다리며 다시 보낸다.
  다시 보낼 때마다 부른 쪽이 준 on_retry에 알린다(작업 기록에 적으려고). 이 파일은 작업을 모른다.
- 캐시: 같은 문장(문장 해시)은 모델마다 한 번만 계산한다. 무엇이 빠졌는지 찾고(find_missing_hashes),
  한꺼번에 저장하고(save_vectors), 한꺼번에 읽는다(load_vectors). 데이터셋 · 모듈이 모두 같이 쓴다.

같은 이름의 모델이 다른 차원으로 등록돼 있으면 저장해 둔 임베딩과 섞이면 안 되므로 '실패'로 알린다.
켜는 것을 멈추지는 않는다(예전에는 작업 실행기가 멈췄다). 임베딩만 쓸 수 없는 상태로 둔다.
계산 중에 받은 벡터의 길이가 등록된 차원과 다르면 저장하지 않고 ExternalServiceError를 낸다.
"""

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
import numpy as np
from sqlalchemy import LargeBinary, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system.connections import ConnectionCheck, describe_failure
from dent.system.exceptions import ExternalServiceError, InvalidInputError
from dent.system.models import Connection, Embedding, EmbeddingModel

logger = logging.getLogger(__name__)

# 연결을 확인할 때 보내는 문장
PROBE_TEXT = "연결 확인"

# 한 번에 보낼 문장 수. 임베딩 계산을 만들 때 쓴다.
EMBEDDING_BATCH_SIZE = 128

# 동시에 보낼 요청 수. 외부 서버를 과하게 누르지 않게 한다.
EMBEDDING_MAX_CONCURRENCY = 4

# 요청 하나의 시간 한도(초). 로컬 서버는 첫 요청에 모델을 불러오느라 몇 초 걸릴 수 있다.
EMBEDDING_TIMEOUT_S = 30.0

# 서버에 붙을 때 기다리는 시간(초). 주소가 틀렸을 때 화면이 오래 멈춰 있지 않게 짧게 둔다.
CONNECT_TIMEOUT_S = 3.0

# 1초 = 1000밀리초
MS_PER_S = 1000

# 요청 하나를 보내 보는 최대 횟수. 로컬 서버가 잠깐 바쁘거나 모델을 다시 불러오는 동안을 넘기려고.
EMBEDDING_MAX_ATTEMPTS = 3

# 다시 보내기 전에 기다리는 시간(초). 두 번째는 두 배를 기다린다(1초 → 2초).
EMBEDDING_RETRY_BASE_S = 1.0

# 다시 보낼 때 부르는 함수: (몇 번째 실패인지, 실패 까닭 한 마디). 예: (1, "시간 초과")
type RetryHandler = Callable[[int, str], Awaitable[None]]

# 다시 보내 볼 만한 HTTP 상태: 너무 많은 요청(429)과 서버 쪽 문제(5xx)
TOO_MANY_REQUESTS = 429
SERVER_ERROR_MIN = 500

# 캐시에서 해시를 찾거나 벡터를 읽을 때 한 번에 묻는 수. 쿼리 하나의 인자 · 응답이 너무 커지지 않게.
CACHE_QUERY_CHUNK = 5_000

# halfvec을 이진 모양(halfvec_send)으로 읽으면 앞 4바이트는 차원(2바이트)과 빈칸(2바이트)이고,
# 그 뒤가 큰 끝(big-endian) 반 정밀도 수들이다.
HALFVEC_HEADER_BYTES = 4
HALFVEC_ITEM_TYPE = ">f2"

MISSING_VECTORS_MESSAGE = "저장된 임베딩이 모자랍니다. 다시 만들어 주세요."


async def check_embedding_server(
    connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None
) -> ConnectionCheck:
    """짧은 문장 하나로 임베딩을 받아 본다. facts: dim · latency_ms. 예외를 내지 않는다."""
    if not connection.model:
        return ConnectionCheck(ok=False, detail="모델 이름 없음")

    url = f"{connection.base_url}/embeddings"
    payload = {"model": connection.model, "input": [PROBE_TEXT]}
    timeout = httpx.Timeout(EMBEDDING_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        return ConnectionCheck(ok=False, detail=describe_failure(error))
    latency_ms = round((time.perf_counter() - started) * MS_PER_S)

    is_vector = isinstance(vector, list) and len(vector) > 0
    if not is_vector:
        return ConnectionCheck(ok=False, detail="응답 모양 다름")
    dim = len(vector)
    return ConnectionCheck(
        ok=True,
        detail=f"{dim}차원 · {latency_ms}ms",
        facts={"dim": dim, "latency_ms": latency_ms},
    )


async def check_embedding(
    db: AsyncSession,
    connection: Connection,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionCheck:
    """서버를 확인하고, 같은 이름으로 등록된 모델과 차원이 다르면 실패로 바꾼다. 예외를 내지 않는다."""
    result = await check_embedding_server(connection, transport=transport)
    dim = result.facts.get("dim")
    if not result.ok or not isinstance(dim, int) or not connection.model:
        return result

    registered = await get_registered_model(db, name=connection.model)
    if registered is None or registered.dim == dim:
        return result
    return ConnectionCheck(
        ok=False,
        detail=f"차원 불일치 · 등록 {registered.dim} · 지금 {dim}",
        facts=result.facts,
    )


async def get_registered_model(db: AsyncSession, *, name: str) -> EmbeddingModel | None:
    """이름으로 등록된 임베딩 모델. 없으면 None."""
    return await db.scalar(select(EmbeddingModel).where(EmbeddingModel.name == name))


async def register_model(db: AsyncSession, *, name: str, dim: int) -> bool:
    """처음 보는 모델이면 이름과 차원을 등록하고 True. 이미 있으면 아무것도 하지 않고 False.

    차원이 같은지는 check_embedding이 먼저 본다. 여기서는 겹쳐 넣지만 않게 한다.
    """
    if await get_registered_model(db, name=name) is not None:
        return False
    db.add(EmbeddingModel(name=name, dim=dim))
    try:
        await db.commit()
    except IntegrityError:
        # API 서버와 작업 실행기가 동시에 등록하면 한쪽이 UNIQUE에 걸린다. 이미 등록된 것이다.
        await db.rollback()
        return False
    logger.info("임베딩 모델을 등록했습니다: %s · %d차원", name, dim)
    return True


async def ensure_model(
    db: AsyncSession,
    connection: Connection,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmbeddingModel:
    """연결의 모델을 등록된 모델 줄로 돌려준다. 처음 보는 모델이면 서버에 물어 차원을 알아내 등록한다.

    모델 이름이 없으면 InvalidInputError, 서버에 붙지 못하면 ExternalServiceError.
    """
    if not connection.model:
        raise InvalidInputError("임베딩 연결에 모델 이름이 없습니다. 연결 설정을 확인하세요.")
    registered = await get_registered_model(db, name=connection.model)
    if registered is not None:
        return registered

    result = await check_embedding_server(connection, transport=transport)
    dim = result.facts.get("dim")
    if not result.ok or not isinstance(dim, int):
        raise ExternalServiceError(
            f"임베딩 서버에 붙지 못했습니다({result.detail}). 연결 설정을 확인하세요."
        )
    await register_model(db, name=connection.model, dim=dim)
    registered = await get_registered_model(db, name=connection.model)
    if registered is None:  # 방금 등록했으므로 늘 있다. 타입을 좁히려고 확인한다.
        raise ExternalServiceError("임베딩 모델을 등록하지 못했습니다. 다시 시도해 주세요.")
    return registered


async def embed_texts(
    connection: Connection,
    texts: Sequence[str],
    *,
    on_retry: RetryHandler | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[list[float]]:
    """문장들의 임베딩을 받아 같은 순서로 돌려준다.

    EMBEDDING_BATCH_SIZE개씩 나눠 최대 EMBEDDING_MAX_CONCURRENCY개를 동시에 보낸다.
    다시 보낼 때마다 on_retry를 부른다. 다시 보내도 안 되면 ExternalServiceError.
    """
    batches = list(itertools.batched(texts, EMBEDDING_BATCH_SIZE))
    limit = asyncio.Semaphore(EMBEDDING_MAX_CONCURRENCY)
    timeout = httpx.Timeout(EMBEDDING_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:

        async def send(batch: tuple[str, ...]) -> list[list[float]]:
            async with limit:
                return await _embed_batch(client, connection, list(batch), on_retry=on_retry)

        results = await asyncio.gather(*(send(batch) for batch in batches))
    return [vector for batch_vectors in results for vector in batch_vectors]


async def find_missing_hashes(
    db: AsyncSession, *, model_id: int, text_hashes: Sequence[str]
) -> list[str]:
    """이 모델로 아직 저장하지 않은 문장 해시들. 준 순서를 지킨다."""
    found: set[str] = set()
    for chunk in itertools.batched(text_hashes, CACHE_QUERY_CHUNK):
        query = select(Embedding.text_hash).where(
            Embedding.model_id == model_id, Embedding.text_hash.in_(chunk)
        )
        found.update(await db.scalars(query))
    return [text_hash for text_hash in text_hashes if text_hash not in found]


async def save_vectors(
    db: AsyncSession,
    *,
    model: EmbeddingModel,
    text_hashes: Sequence[str],
    vectors: Sequence[Sequence[float]],
) -> None:
    """문장 해시마다 벡터를 저장한다. 이미 있으면 그대로 둔다. 커밋은 부른 쪽이 한다.

    벡터 길이가 등록된 모델의 차원과 다르면 저장하지 않고 ExternalServiceError.
    """
    for vector in vectors:
        is_other_dim = len(vector) != model.dim
        if is_other_dim:
            raise ExternalServiceError(
                f"임베딩 차원이 등록된 모델과 다릅니다(등록 {model.dim} · 받은 {len(vector)}). "
                "같은 이름의 다른 모델을 연결했는지 연결 설정을 확인하세요."
            )
    if not vectors:
        return
    rows = [
        {"model_id": model.id, "text_hash": text_hash, "vector": list(vector)}
        for text_hash, vector in zip(text_hashes, vectors, strict=True)
    ]
    # 다른 데이터셋의 작업이 같은 문장을 먼저 저장했을 수 있다. 같은 문장이면 벡터도 같으므로 건너뛴다.
    statement = insert(Embedding).on_conflict_do_nothing(
        index_elements=[Embedding.model_id, Embedding.text_hash]
    )
    await db.execute(statement, rows)


async def load_vectors(
    db: AsyncSession, *, model: EmbeddingModel, text_hashes: Sequence[str]
) -> np.ndarray:
    """문장 해시 순서대로 벡터를 (문장 수, 차원) float32 배열로 돌려준다.

    수십만 건이어도 메모리가 넘치지 않게 CACHE_QUERY_CHUNK개씩 이진 모양으로 읽어 미리 만든 배열에 채운다
    (글자 모양 '[0.1,…]'로 읽으면 파이썬 수로 바뀌며 몇 배 느리고 무겁다).
    하나라도 없으면 ExternalServiceError.
    """
    matrix = np.empty((len(text_hashes), model.dim), dtype=np.float32)
    position = {text_hash: index for index, text_hash in enumerate(text_hashes)}
    filled = 0
    raw_vector = func.halfvec_send(Embedding.vector, type_=LargeBinary)
    for chunk in itertools.batched(text_hashes, CACHE_QUERY_CHUNK):
        query = select(Embedding.text_hash, raw_vector).where(
            Embedding.model_id == model.id, Embedding.text_hash.in_(chunk)
        )
        for text_hash, raw in await db.execute(query):
            values = np.frombuffer(raw, dtype=HALFVEC_ITEM_TYPE, offset=HALFVEC_HEADER_BYTES)
            if values.shape[0] != model.dim:
                raise ExternalServiceError(MISSING_VECTORS_MESSAGE)
            matrix[position[text_hash]] = values
            filled += 1
    if filled != len(text_hashes):
        raise ExternalServiceError(MISSING_VECTORS_MESSAGE)
    return matrix


async def _embed_batch(
    client: httpx.AsyncClient,
    connection: Connection,
    texts: list[str],
    *,
    on_retry: RetryHandler | None = None,
) -> list[list[float]]:
    """문장 한 묶음을 보내고 벡터를 받는다. 잠깐의 문제는 기다렸다 다시 보낸다. 안 되면 ExternalServiceError."""
    url = f"{connection.base_url}/embeddings"
    payload = {"model": connection.model, "input": texts}
    for attempt in range(1, EMBEDDING_MAX_ATTEMPTS + 1):
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return _read_vectors(response.json(), count=len(texts))
        except httpx.HTTPError as error:
            is_last = attempt == EMBEDDING_MAX_ATTEMPTS
            if is_last or not _is_worth_retry(error):
                raise ExternalServiceError(
                    f"임베딩 서버 요청이 실패했습니다({describe_failure(error)}). "
                    "연결 설정과 서버를 확인하세요."
                ) from None
            logger.warning("임베딩 요청 %d번째 실패, 다시 보냅니다: %s", attempt, error)
            if on_retry is not None:
                await on_retry(attempt, describe_failure(error))
            await asyncio.sleep(EMBEDDING_RETRY_BASE_S * 2 ** (attempt - 1))
        except (KeyError, IndexError, TypeError, ValueError):
            raise ExternalServiceError(
                "임베딩 서버의 응답 모양이 다릅니다. OpenAI 호환 /embeddings 서버인지 확인하세요."
            ) from None
    # 위 반복은 돌려주거나 예외를 내고 끝난다. 타입 검사기를 위해 남긴다.
    raise ExternalServiceError("임베딩 서버 요청이 실패했습니다.")


def _is_worth_retry(error: httpx.HTTPError) -> bool:
    """다시 보내 볼 만한 실패인지: 연결 · 시간 초과, 429, 5xx."""
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return code == TOO_MANY_REQUESTS or code >= SERVER_ERROR_MIN
    return isinstance(error, httpx.TransportError)


def _read_vectors(body: dict[str, Any], *, count: int) -> list[list[float]]:
    """OpenAI 호환 응답에서 벡터를 보낸 순서대로 꺼낸다. 수가 다르면 ValueError."""
    items = body["data"]
    # 대부분 보낸 순서로 오지만, 규격은 index로 순서를 알려 준다.
    ordered = sorted(items, key=lambda item: item.get("index", 0))
    vectors = [item["embedding"] for item in ordered]
    is_right_count = len(vectors) == count
    is_all_vectors = all(isinstance(vector, list) and vector for vector in vectors)
    if not is_right_count or not is_all_vectors:
        raise ValueError("응답 모양 다름")
    return vectors
