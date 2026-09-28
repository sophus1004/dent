"""외부 모델 서버 연결 (임베딩 · Jev · LLM). 화면의 연결 설정이 저장하고, 모든 곳이 여기서 읽는다.

연결 값은 system_connections 한 곳에만 둔다(.env에 두지 않는다). 저장하면 다시 실행하지 않아도
다음 확인부터 새 값을 쓴다: API 서버는 /readyz를 만들 때, 작업 실행기는 몇 초마다 이 테이블을 읽는다.
연결을 확인한 결과(ConnectionCheck)의 모양과, 확인이 실패한 까닭을 낱말로 바꾸는 일도 여기에 둔다.
확인 자체는 서버 종류마다 embedding.py · jev.py · llm.py가 하고, 기억(1분)은 status.py가 한다.

LLM은 공급자(provider)와 API 키(api_key)도 적는다. 키를 비워 두고 저장 · 확인하면 저장된 키를 그대로 쓴다
(모델만 바꿀 때 키를 다시 넣지 않게). 다만 공급자나 주소가 바뀌면 저장된 키를 쓰지 않는다(다른 서버로 키가 가지 않게).

실행할 때 내장 모델을 올린 역할은 그것이 이긴다: 연결을 읽는 곳(get_connection · list_connections)은 저장된 줄 대신
내장 모델 서버로 가는 연결을 받고, 저장 · 끊기는 ConflictError다. 저장된 줄은 지우지 않으므로
다음에 실행할 때 '올리지 않음'을 고르면 그 연결이 다시 쓰인다.
"""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import embedded_models
from dent.system.config import get_settings
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import Connection, ConnectionRole, LlmProvider

# 서버 주소로 받는 방식
WEB_SCHEMES = ("http", "https")

INVALID_URL_MESSAGE = "주소는 http:// 또는 https://로 시작해야 합니다. 예: http://127.0.0.1:8010"
EMBEDDING_MODEL_REQUIRED_MESSAGE = "임베딩은 모델 이름이 필요합니다. 예: bge-m3"
LLM_PROVIDER_REQUIRED_MESSAGE = "LLM 공급자를 골라 주세요."
LLM_MODEL_REQUIRED_MESSAGE = "LLM은 모델 이름이 필요합니다. [연결 확인]으로 목록을 받아 고르세요."
CONNECTION_NOT_FOUND_MESSAGE = "저장된 연결이 없습니다."
EMBEDDED_ROLE_MESSAGE = (
    "내장 모델을 쓰는 역할이라 여기서 바꿀 수 없습니다. 다시 실행할 때 '올리지 않음'을 고르세요."
)

# 확인 결과의 값 하나. 차원·걸린 시간(수), 장치(글자), 불러 둔 모델 목록 …
FactValue = str | int | float | list[str] | None


@dataclass(frozen=True)
class ConnectionCheck:
    """외부 서버 하나를 확인한 결과."""

    # 쓸 수 있는지
    ok: bool

    # 짧은 낱말 줄. 되면 사실(예: '1024차원 · 42ms'), 안 되면 까닭(예: '연결 거부').
    # 화면은 앞에 '연결됨' · '실패'를 붙여 그대로 보인다.
    detail: str

    # 확인하며 알아낸 값. 임베딩: dim · latency_ms, Jev: device · loaded · latency_ms · model
    facts: dict[str, FactValue] = field(default_factory=dict)


def new_connection(
    *,
    role: ConnectionRole,
    base_url: str,
    model: str | None,
    provider: LlmProvider | None = None,
    api_key: str | None = None,
) -> Connection:
    """저장하지 않은 연결 하나를 만든다. 확인만 할 때 쓴다.

    주소가 틀리거나, 임베딩에 모델이 없거나, LLM에 공급자가 없으면 InvalidInputError.
    LLM이 아니면 공급자는 버린다.
    """
    cleaned_model = (model or "").strip() or None
    is_embedding = role is ConnectionRole.EMBEDDING
    if is_embedding and cleaned_model is None:
        raise InvalidInputError(EMBEDDING_MODEL_REQUIRED_MESSAGE)
    is_llm = role is ConnectionRole.LLM
    if is_llm and provider is None:
        raise InvalidInputError(LLM_PROVIDER_REQUIRED_MESSAGE)
    return Connection(
        role=role.value,
        base_url=normalize_base_url(base_url),
        model=cleaned_model,
        provider=provider.value if is_llm and provider is not None else None,
        api_key=(api_key or "").strip() or None,
    )


async def connection_to_check(
    db: AsyncSession,
    *,
    role: ConnectionRole,
    base_url: str,
    model: str | None,
    provider: LlmProvider | None = None,
    api_key: str | None = None,
) -> Connection:
    """화면의 입력으로 확인할 연결(저장하지 않는다). 키를 비웠으면 저장된 키를 쓴다(같은 공급자 · 주소일 때만)."""
    connection = new_connection(
        role=role, base_url=base_url, model=model, provider=provider, api_key=api_key
    )
    connection.api_key = await _key_to_use(db, connection)
    return connection


def normalize_base_url(value: str) -> str:
    """주소를 검사하고 끝의 /를 뗀다. http·https가 아니거나 서버 이름이 없으면 InvalidInputError."""
    cleaned = value.strip().rstrip("/")
    parsed = urlsplit(cleaned)
    is_web_address = parsed.scheme in WEB_SCHEMES and bool(parsed.hostname)
    if not is_web_address:
        raise InvalidInputError(INVALID_URL_MESSAGE)
    return cleaned


async def get_connection(db: AsyncSession, *, role: ConnectionRole) -> Connection | None:
    """쓸 연결 하나: 내장 모델을 올린 역할이면 그 서버로 가는 연결, 아니면 저장된 연결. 없으면 None(미연결)."""
    embedded = embedded_models.embedded_connection(get_settings(), role)
    if embedded is not None:
        return embedded
    return await _saved_connection(db, role=role)


async def list_connections(db: AsyncSession) -> list[Connection]:
    """쓸 연결 모두(내장 모델을 올린 역할은 그 서버로 가는 연결). 역할 이름 순서."""
    settings = get_settings()
    found: list[Connection] = []
    for row in await db.scalars(select(Connection).order_by(Connection.role)):
        is_embedded_role = embedded_models.chosen_model(ConnectionRole(row.role)) is not None
        if not is_embedded_role:
            found.append(row)
    for role in ConnectionRole:
        embedded = embedded_models.embedded_connection(settings, role)
        if embedded is not None:
            found.append(embedded)
    return sorted(found, key=lambda connection: connection.role)


def refuse_embedded_role(role: ConnectionRole) -> None:
    """내장 모델을 올린 역할이면 ConflictError(화면에서 저장 · 끊기를 막는다)."""
    if embedded_models.chosen_model(role) is not None:
        raise ConflictError(EMBEDDED_ROLE_MESSAGE)


async def _saved_connection(db: AsyncSession, *, role: ConnectionRole) -> Connection | None:
    """저장된 연결 하나(실행할 때 올린 내장 모델은 보지 않는다). 없으면 None."""
    # 다른 곳(화면·작업 실행기)에서 바꿨을 수 있으므로 세션이 기억한 값 대신 DB 값을 읽는다.
    return await db.get(Connection, role.value, populate_existing=True)


async def save_connection(
    db: AsyncSession,
    *,
    role: ConnectionRole,
    base_url: str,
    model: str | None,
    provider: LlmProvider | None = None,
    api_key: str | None = None,
) -> Connection:
    """연결을 저장한다(없으면 만들고 있으면 바꾼다).

    주소가 틀리거나 필요한 값(임베딩 모델 · LLM 공급자 · LLM 모델)이 없으면 InvalidInputError.
    내장 모델을 올린 역할이면 ConflictError. 키를 비웠으면 저장된 키를 그대로 둔다(같은 공급자 · 주소일 때만).
    """
    refuse_embedded_role(role)
    checked = new_connection(
        role=role, base_url=base_url, model=model, provider=provider, api_key=api_key
    )
    is_llm_without_model = role is ConnectionRole.LLM and checked.model is None
    if is_llm_without_model:
        raise InvalidInputError(LLM_MODEL_REQUIRED_MESSAGE)
    values = {
        "base_url": checked.base_url,
        "model": checked.model,
        "provider": checked.provider,
        "api_key": await _key_to_use(db, checked),
    }
    # 두 창에서 동시에 저장해도 부딪치지 않게 '넣거나 바꾸기'를 한 문장으로 보낸다.
    statement = (
        insert(Connection)
        .values(role=role.value, **values)
        .on_conflict_do_update(
            index_elements=[Connection.role], set_={**values, "updated_at": func.now()}
        )
    )
    await db.execute(statement)
    await db.commit()
    saved = await _saved_connection(db, role=role)
    if saved is None:  # 방금 저장했으므로 늘 있다. 타입을 좁히려고 확인한다.
        raise NotFoundError(CONNECTION_NOT_FOUND_MESSAGE)
    return saved


async def delete_connection(db: AsyncSession, *, role: ConnectionRole) -> None:
    """연결을 지운다(연결 끊기). 저장된 연결이 없으면 NotFoundError, 내장 모델을 올린 역할이면 ConflictError."""
    refuse_embedded_role(role)
    connection = await _saved_connection(db, role=role)
    if connection is None:
        raise NotFoundError(CONNECTION_NOT_FOUND_MESSAGE)
    await db.delete(connection)
    await db.commit()


async def _key_to_use(db: AsyncSession, connection: Connection) -> str | None:
    """새로 준 키가 있으면 그것, 없으면 같은 공급자 · 주소로 저장된 키. 공급자나 주소가 바뀌었으면 None."""
    if connection.api_key:
        return connection.api_key
    saved = await _saved_connection(db, role=ConnectionRole(connection.role))
    is_same_server = (
        saved is not None
        and saved.provider == connection.provider
        and saved.base_url == connection.base_url
    )
    return saved.api_key if is_same_server and saved is not None else None


def describe_failure(error: Exception) -> str:
    """외부 서버 요청이 실패한 까닭을 짧은 낱말로. 예: '연결 거부', '시간 초과', 'HTTP 401'."""
    if isinstance(error, httpx.ConnectError):
        return "연결 거부"
    if isinstance(error, httpx.TimeoutException):
        return "시간 초과"
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return "연결 실패"
    # JSON이 아니거나 필요한 칸이 없다(KeyError · TypeError · ValueError …)
    return "응답 모양 다름"
