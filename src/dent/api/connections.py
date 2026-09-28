"""외부 모델 연결 API (/api/v1/connections). 화면의 연결 설정이 쓴다.

역할은 셋이다: embedding(임베딩 서버) · jev(Jev 판정 서버) · llm(LLM, 공급자 · API 키 포함).
저장하면 바로 확인해 결과를 돌려주고, 상태(/readyz)도 다시 실행하지 않고 바로 새 연결로 바뀐다.
API 키는 응답에 싣지 않는다(api_key_hint로 앞뒤 몇 글자만).
실행할 때 내장 모델을 올린 역할은 embedded에 그 이름을 싣고, 저장 · 끊기는 409다(화면이 그 줄을 잠근다).
"""

from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import connections as connections_service
from dent.system import embedded_models, llm
from dent.system import status as status_service
from dent.system.connections import ConnectionCheck
from dent.system.db import get_db
from dent.system.models import Connection, ConnectionRole
from dent.system.schemas import ConnectionCheckRead, ConnectionRead, ConnectionUpdate

router = APIRouter(prefix="/connections", tags=["연결"])


def get_http_transport() -> httpx.AsyncBaseTransport | None:
    """외부 서버에 붙을 때 쓸 전송 계층. 평소에는 None(실제 네트워크)이고 테스트가 가짜로 바꾼다."""
    return None


Db = Annotated[AsyncSession, Depends(get_db)]
Transport = Annotated[httpx.AsyncBaseTransport | None, Depends(get_http_transport)]


@router.get("")
async def list_connections(db: Db, transport: Transport) -> list[ConnectionRead]:
    """세 역할(임베딩 · Jev · LLM)의 연결과 마지막 확인 결과. 저장하지 않은 역할은 주소가 null."""
    statuses = await status_service.list_connection_statuses(db, transport=transport)
    return [_connection_read(item.role, item.connection, item.check) for item in statuses]


@router.post("/{role}/check")
async def check_connection(
    role: ConnectionRole, data: ConnectionUpdate, db: Db, transport: Transport
) -> ConnectionCheckRead:
    """저장하지 않고 연결만 확인한다. 키를 비웠으면 저장된 키를 쓴다. 주소 모양이 틀리면 422."""
    connection = await connections_service.connection_to_check(
        db,
        role=role,
        base_url=data.base_url,
        model=data.model,
        provider=data.provider,
        api_key=data.api_key,
    )
    result = await status_service.check_connection(db, connection, transport=transport)
    return ConnectionCheckRead.model_validate(asdict(result))


@router.put("/{role}")
async def save_connection(
    role: ConnectionRole, data: ConnectionUpdate, db: Db, transport: Transport
) -> ConnectionRead:
    """연결을 저장하고 바로 확인한다. 확인이 실패해도 저장은 된다.

    주소 모양이 틀리면 422, 내장 모델을 올린 역할이면 409.
    """
    connection = await connections_service.save_connection(
        db,
        role=role,
        base_url=data.base_url,
        model=data.model,
        provider=data.provider,
        api_key=data.api_key,
    )
    result = await status_service.connection_status(
        db, connection, recheck=True, transport=transport
    )
    return _connection_read(role, connection, result)


@router.delete("/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(role: ConnectionRole, db: Db) -> None:
    """연결을 끊는다(저장한 연결을 지운다). 저장된 연결이 없으면 404, 내장 모델을 올린 역할이면 409."""
    await connections_service.delete_connection(db, role=role)
    status_service.forget_connection_status(role)


def _connection_read(
    role: ConnectionRole, connection: Connection | None, check: ConnectionCheck | None
) -> ConnectionRead:
    """연결과 확인 결과를 응답 모양으로. 연결이 없으면 빈 줄."""
    embedded = embedded_models.chosen_model(role)
    return ConnectionRead(
        role=role,
        embedded=None if embedded is None else embedded.key,
        base_url=connection.base_url if connection else None,
        model=connection.model if connection else None,
        provider=connection.provider if connection else None,
        api_key_hint=llm.api_key_hint(connection.api_key) if connection else None,
        updated_at=connection.updated_at if connection else None,
        check=ConnectionCheckRead.model_validate(asdict(check)) if check else None,
    )
