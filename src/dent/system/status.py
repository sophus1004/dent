"""실행된 뒤의 상태. API 서버의 /readyz가 이것을 돌려준다.

DB 연결, 테이블 버전, storage는 '쓸 준비'의 조건이다. 하나라도 안 되면 준비되지 않은 것이다.
작업 실행기와 외부 모델 서버(임베딩 · Jev · LLM)는 참고로만 알려 준다. 이것들이 안 돼도 데이터를 보고 고칠 수는 있다.

외부 서버 연결은 system_connections에서 읽고, 확인 결과를 1분 동안 기억한다(화면이 부를 때마다 누르지 않게).
기억은 (주소, 모델, 공급자, 키)를 함께 적어 두므로 연결을 바꾸면 바로 새로 확인한다. 저장 · 삭제할 때도 기억을 지운다.
그래서 연결을 바꿔도 다시 실행할 필요가 없다.

내장 모델(실행할 때 올린 것)은 먼저 모델 서버의 /health를 읽는다. 내려받는 중 · 불러오는 중이면
그 단계와 진행을 돌려주고 기억하지 않는다(준비되는 대로 바로 보이게). 준비되면 외부 서버와 똑같이 확인한다.
홈의 모델 칸은 역할마다 한 줄(ModelInfo: 내장 · 모델 · 공급자 · 상태 · 진행)을 /readyz의 models로 받는다.
"""

import time
from dataclasses import dataclass, field, replace

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import connections, embedded_models, embedding, jev, jobs, llm
from dent.system.checks import check_schema as check_schema_version
from dent.system.checks import check_storage, check_vector, connect_database
from dent.system.config import Settings, get_settings
from dent.system.connections import ConnectionCheck
from dent.system.db import get_engine, get_sessionmaker
from dent.system.embedded_models import ServerPhase
from dent.system.exceptions import StartupError
from dent.system.models import Connection, ConnectionRole

# 외부 서버 상태를 다시 확인하는 간격(초). 화면이 부를 때마다 외부 서버를 누르지 않게.
CONNECTION_STATUS_TTL_S = 60.0

# /readyz info의 연결 상태 낱말. 화면은 ' · ' 앞의 낱말로 상태를 가른다.
CONNECTED_WORD = "연결됨"
FAILED_WORD = "실패"
NOT_CONNECTED_WORD = "미연결"

# 내장 모델 서버가 준비되기 전의 상태 낱말. 화면은 ' · ' 앞의 낱말로 상태를 가른다.
DOWNLOADING_WORD = "내려받는 중"
VERIFYING_WORD = "확인하는 중"
LOADING_WORD = "불러오는 중"
PREPARING_WORDS = {
    ServerPhase.DOWNLOADING: DOWNLOADING_WORD,
    ServerPhase.VERIFYING: VERIFYING_WORD,
    ServerPhase.LOADING: LOADING_WORD,
}

# 내장 모델 서버가 답하지 않을 때의 까닭 (런처 없이 API 서버만 실행했거나 모델 서버가 멈췄다)
MODEL_SERVER_OFF_DETAIL = "모델 서버 종료됨"

# DB에 붙지 못해 확인하지 못한 항목의 설명
AFTER_DATABASE_DETAIL = "DB에 붙은 뒤에 확인합니다."

# 1GB (저장 공간 · 모델 파일 크기를 GB로 보인다)
BYTES_PER_GB = 1024**3

# 100% (내려받기 진행)
PERCENT = 100

# /readyz가 확인하는 외부 서버
CHECKED_ROLES = (ConnectionRole.EMBEDDING, ConnectionRole.JEV, ConnectionRole.LLM)


@dataclass(frozen=True)
class Check:
    """확인 하나의 결과."""

    # 되는지
    ok: bool

    # 사람이 읽을 한 줄
    detail: str


@dataclass(frozen=True)
class ModelInfo:
    """홈의 모델 칸 한 줄: 역할 하나의 모델과 상태."""

    # 역할: embedding · jev · llm
    role: str

    # 내장 모델 이름(bge-m3 · laya). 내장이 아니면 None
    embedded: str | None

    # 연결 주소. 미연결이면 None
    base_url: str | None

    # 모델 이름. 비어 있으면 None(Jev 자동 고르기 · 미연결)
    model: str | None

    # LLM 공급자. 없으면 None
    provider: str | None

    # 상태: ok · downloading · verifying · loading · failed · off(미연결)
    state: str

    # 값이나 까닭. 예: '1024차원 · 47ms', '연결 거부'. 없으면 ''
    detail: str

    # 파일 받기 · 확인: 한 · 전체 바이트와 남은 초. 받거나 확인하는 중이 아니면 None
    done_bytes: int | None = None
    total_bytes: int | None = None
    eta_s: int | None = None

    # 쓰는 장치 (mps · cuda · cpu). 모르면 None
    device: str | None = None


@dataclass
class Readiness:
    """쓸 준비가 됐는지와 그 근거."""

    # DB·테이블·storage가 모두 되면 True
    ready: bool

    # 준비의 조건이 되는 확인들
    checks: dict[str, Check] = field(default_factory=dict)

    # 참고로만 알려 주는 상태 (작업 실행기, 임베딩, Jev, LLM)
    info: dict[str, Check] = field(default_factory=dict)

    # 홈의 모델 칸: 임베딩 · Jev · LLM 순서로 한 줄씩
    models: list[ModelInfo] = field(default_factory=list)


@dataclass(frozen=True)
class ConnectionStatus:
    """역할 하나의 연결과 그 상태."""

    # 역할: embedding · jev · llm
    role: ConnectionRole

    # 저장된 연결. 없으면 None(미연결).
    connection: Connection | None

    # 마지막 확인 결과. 연결이 없으면 None.
    check: ConnectionCheck | None


# 역할별 마지막 확인: (확인한 시각, 연결 값(_target_of), 결과)
_connection_cache: dict[ConnectionRole, tuple[float, tuple[str | None, ...], ConnectionCheck]] = {}


async def collect_readiness(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> Readiness:
    """지금 쓸 준비가 됐는지 모은다. 확인마다 한 번씩만 시도하고 예외를 내지 않는다."""
    checks: dict[str, Check] = {}
    try:
        storage = check_storage(settings, warn_low_disk=False)
        parts = [str(storage.path), f"여유 {storage.free_bytes // BYTES_PER_GB}GB"]
        model_bytes = embedded_models.folder_bytes(settings.embedded_models_path)
        if model_bytes:
            parts.append(f"모델 {model_bytes / BYTES_PER_GB:.1f}GB")
        checks["storage"] = Check(ok=True, detail=" · ".join(parts))
    except StartupError as error:
        checks["storage"] = Check(ok=False, detail=error.message)

    target = settings.database_target()
    engine = get_engine()
    try:
        version = await connect_database(engine, target=target, attempts=1)
        async with engine.connect() as conn:
            vector = await check_vector(conn, target=target)
            checks["db"] = Check(
                ok=True, detail=f"{target} · PostgreSQL {version} · vector {vector}"
            )
            try:
                revision = await check_schema_version(conn)
                checks["schema"] = Check(ok=True, detail=f"최신 ({revision})")
            except StartupError as error:
                checks["schema"] = Check(ok=False, detail=error.message)
    except StartupError as error:
        checks["db"] = Check(ok=False, detail=error.message)
        checks["schema"] = Check(ok=False, detail=AFTER_DATABASE_DETAIL)

    info: dict[str, Check] = {}
    models: list[ModelInfo] = []
    if checks["db"].ok and checks["schema"].ok:
        info["worker"] = await _worker_status()
        async with get_sessionmaker()() as session:
            for role in CHECKED_ROLES:
                check, model = await _connection_info(session, role, transport=transport)
                info[role.value] = check
                models.append(model)
    else:
        info["worker"] = Check(ok=False, detail=AFTER_DATABASE_DETAIL)
        for role in CHECKED_ROLES:
            info[role.value] = Check(ok=False, detail=AFTER_DATABASE_DETAIL)
            embedded = embedded_models.chosen_model(role)
            models.append(
                ModelInfo(
                    role=role.value,
                    embedded=None if embedded is None else embedded.key,
                    base_url=None,
                    model=None,
                    provider=None,
                    state="off",
                    detail=AFTER_DATABASE_DETAIL,
                )
            )

    ready = all(check.ok for check in checks.values())
    return Readiness(ready=ready, checks=checks, info=info, models=models)


async def _worker_status() -> Check:
    """작업 실행기가 실행 중인지와 대기 작업 수."""
    async with get_sessionmaker()() as session:
        running = await jobs.is_worker_running(session)
        queued = await jobs.count_queued(session)
    if running:
        return Check(ok=True, detail=f"실행 중 · 대기 작업 {queued}건")
    return Check(
        ok=False,
        detail=f"실행 중이 아닙니다. 가져오기와 분석이 멈춰 있습니다 (대기 작업 {queued}건).",
    )


async def _connection_info(
    db: AsyncSession, role: ConnectionRole, *, transport: httpx.AsyncBaseTransport | None
) -> tuple[Check, ModelInfo]:
    """/readyz에 보일 연결 한 줄과 홈의 모델 칸 한 줄.

    연결 한 줄: '연결됨 · 1024차원 · 42ms' · '실패 · 연결 거부' · '미연결' · '내려받는 중 · 43% · 0.9 / 2.2GB'.
    """
    connection = await connections.get_connection(db, role=role)
    embedded = embedded_models.chosen_model(role)
    if connection is None:
        model = _model_info(role, embedded=None, connection=None, state="off", detail="")
        return Check(ok=False, detail=NOT_CONNECTED_WORD), model
    result = await connection_status(db, connection, transport=transport)
    phase = result.facts.get("phase")
    preparing_word = PREPARING_WORDS.get(ServerPhase(phase)) if isinstance(phase, str) else None
    if preparing_word is not None:
        check = Check(ok=False, detail=_joined(preparing_word, result.detail))
        state = str(phase)
    else:
        word = CONNECTED_WORD if result.ok else FAILED_WORD
        check = Check(ok=result.ok, detail=_joined(word, result.detail))
        state = "ok" if result.ok else "failed"
    model = _model_info(
        role,
        embedded=embedded,
        connection=connection,
        state=state,
        detail=result.detail,
        facts=result.facts,
    )
    return check, model


def _joined(word: str, detail: str) -> str:
    """상태 낱말과 값을 ' · '로 잇는다. 값이 없으면 낱말만."""
    return f"{word} · {detail}" if detail else word


def _model_info(
    role: ConnectionRole,
    *,
    embedded: embedded_models.EmbeddedModel | None,
    connection: Connection | None,
    state: str,
    detail: str,
    facts: dict[str, connections.FactValue] | None = None,
) -> ModelInfo:
    """홈의 모델 칸 한 줄. 내려받기 진행과 장치는 확인 결과(facts)에서 꺼낸다."""
    facts = facts or {}

    def number(name: str) -> int | None:
        value = facts.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    device = facts.get("device")
    return ModelInfo(
        role=role.value,
        embedded=None if embedded is None else embedded.key,
        base_url=None if connection is None else connection.base_url,
        model=None if connection is None else connection.model,
        provider=None if connection is None else connection.provider,
        state=state,
        detail=detail,
        done_bytes=number("done_bytes"),
        total_bytes=number("total_bytes"),
        eta_s=number("eta_s"),
        device=device if isinstance(device, str) else None,
    )


async def check_connection(
    db: AsyncSession,
    connection: Connection,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionCheck:
    """연결 하나를 지금 확인한다(기억을 쓰지 않는다). 저장하지 않은 연결도 된다. 예외를 내지 않는다."""
    role = ConnectionRole(connection.role)
    if role is ConnectionRole.EMBEDDING:
        return await embedding.check_embedding(db, connection, transport=transport)
    if role is ConnectionRole.JEV:
        return await jev.check_jev_server(connection, transport=transport)
    return await llm.check_llm(connection, transport=transport)


async def connection_status(
    db: AsyncSession,
    connection: Connection,
    *,
    recheck: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionCheck:
    """저장된 연결의 상태. 1분 안에 같은 주소 · 모델로 확인한 결과가 있으면 그것을 쓴다.

    recheck면 기억을 쓰지 않고 새로 확인한다(저장 직후). 내장 모델이면 모델 서버의 단계를 먼저 본다:
    준비되기 전이면 facts.phase(downloading · loading · failed)와 진행을 담아 돌려주고 기억하지 않는다.
    준비됐으면 외부 서버처럼 확인하고 장치(facts.device)를 붙인다.
    """
    role = ConnectionRole(connection.role)
    settings = get_settings()
    server: embedded_models.ServerState | None = None
    if embedded_models.chosen_model(role) is not None:
        server = await embedded_models.read_server(settings, role, transport=transport)
        preparing = _preparing_check(server)
        if preparing is not None:
            return preparing
    result = await _remembered_status(db, connection, recheck=recheck, transport=transport)
    has_device = result.facts.get("device") is not None
    if server is not None and server.device is not None and not has_device:
        result = replace(result, facts={**result.facts, "device": server.device})
    return result


def _preparing_check(server: embedded_models.ServerState | None) -> ConnectionCheck | None:
    """내장 모델 서버가 준비되기 전이면 그 단계의 확인 결과, 준비됐으면 None."""
    if server is None:
        return ConnectionCheck(
            ok=False, detail=MODEL_SERVER_OFF_DETAIL, facts={"phase": ServerPhase.FAILED.value}
        )
    if server.phase is ServerPhase.READY:
        return None
    facts: dict[str, connections.FactValue] = {"phase": server.phase.value, "device": server.device}
    if server.phase is ServerPhase.FAILED:
        return ConnectionCheck(ok=False, detail=server.detail or FAILED_WORD, facts=facts)
    if server.phase is ServerPhase.LOADING:
        return ConnectionCheck(ok=False, detail=server.detail, facts=facts)
    facts |= {
        "done_bytes": server.done_bytes,
        "total_bytes": server.total_bytes,
        "eta_s": server.eta_s,
    }
    percent = server.done_bytes * PERCENT // server.total_bytes if server.total_bytes else 0
    done_gb = server.done_bytes / BYTES_PER_GB
    total_gb = server.total_bytes / BYTES_PER_GB
    return ConnectionCheck(
        ok=False, detail=f"{percent}% · {done_gb:.1f} / {total_gb:.1f}GB", facts=facts
    )


async def _remembered_status(
    db: AsyncSession,
    connection: Connection,
    *,
    recheck: bool,
    transport: httpx.AsyncBaseTransport | None,
) -> ConnectionCheck:
    """1분 안에 같은 연결 값으로 확인한 결과가 있으면 그것, 없거나 recheck면 새로 확인해 기억한다."""
    role = ConnectionRole(connection.role)
    now = time.monotonic()
    cached = _connection_cache.get(role)
    if cached is not None and not recheck:
        checked_at, target, result = cached
        is_same_target = target == _target_of(connection)
        is_recent = now - checked_at < CONNECTION_STATUS_TTL_S
        if is_same_target and is_recent:
            return result
    result = await check_connection(db, connection, transport=transport)
    _connection_cache[role] = (now, _target_of(connection), result)
    return result


def _target_of(connection: Connection) -> tuple[str | None, ...]:
    """확인 결과를 기억할 때 함께 적는 연결 값. 이 가운데 하나라도 바뀌면 새로 확인한다."""
    return (connection.base_url, connection.model, connection.provider, connection.api_key)


def forget_connection_status(role: ConnectionRole | None = None) -> None:
    """기억한 확인 결과를 지운다. 연결을 저장 · 삭제하면 부른다. role이 없으면 모두 지운다."""
    if role is None:
        _connection_cache.clear()
        return
    _connection_cache.pop(role, None)


async def list_connection_statuses(
    db: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[ConnectionStatus]:
    """세 역할(임베딩 · Jev · LLM)의 연결과 상태. 저장하지 않은 역할도 빈 줄로 넣는다."""
    saved = {row.role: row for row in await connections.list_connections(db)}
    statuses: list[ConnectionStatus] = []
    for role in ConnectionRole:
        connection = saved.get(role.value)
        check = None
        if connection is not None:
            check = await connection_status(db, connection, transport=transport)
        statuses.append(ConnectionStatus(role=role, connection=connection, check=check))
    return statuses
