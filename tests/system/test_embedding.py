"""외부 임베딩 서버 테스트. 가짜 서버를 쓴다.

확인: 차원 · 걸린 시간, 거절 · 연결 거부, 차원 불일치, 모델 등록.
계산과 캐시: 묶음 나누기와 순서, 다시 보내기, 반 정밀도 저장 · 읽기, 빠진 해시 찾기, 차원이 다른 벡터 막기.
"""

import httpx
import numpy as np
import pytest
from sqlalchemy import select

from dent.system import embedding
from dent.system.connections import new_connection
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, ConnectionRole, EmbeddingModel
from tests.system.helpers import (
    EMBEDDING_MODEL,
    EMBEDDING_URL,
    embedding_transport,
    refused_transport,
    vector_of,
    vector_transport,
)


def _connection() -> Connection:
    return new_connection(
        role=ConnectionRole.EMBEDDING, base_url=EMBEDDING_URL, model=EMBEDDING_MODEL
    )


async def test_check_embedding_server_reads_dimension_and_latency():
    # 준비
    transport = embedding_transport(dim=8)

    # 실행
    result = await embedding.check_embedding_server(_connection(), transport=transport)

    # 확인
    assert result.ok
    assert result.facts["dim"] == 8
    assert isinstance(result.facts["latency_ms"], int)
    assert result.detail.startswith("8차원 · ")
    sent = transport.requests[0]
    assert str(sent.url) == f"{EMBEDDING_URL}/embeddings"
    assert b'"model":"test-embedding"' in sent.content


async def test_check_embedding_server_reports_rejection():
    # 준비
    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    # 실행
    result = await embedding.check_embedding_server(
        _connection(), transport=httpx.MockTransport(reply)
    )

    # 확인
    assert not result.ok
    assert result.detail == "HTTP 401"


async def test_check_embedding_server_reports_refused_connection():
    # 실행
    result = await embedding.check_embedding_server(_connection(), transport=refused_transport())

    # 확인
    assert not result.ok
    assert result.detail == "연결 거부"


async def test_check_embedding_server_reports_unexpected_reply():
    # 준비
    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vectors": []})

    # 실행
    result = await embedding.check_embedding_server(
        _connection(), transport=httpx.MockTransport(reply)
    )

    # 확인
    assert not result.ok
    assert result.detail == "응답 모양 다름"


async def test_check_embedding_fails_without_raising_when_dimension_changes(db_session):
    # 준비
    await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=1024)

    # 실행
    result = await embedding.check_embedding(
        db_session, _connection(), transport=embedding_transport(dim=768)
    )

    # 확인
    assert not result.ok
    assert result.detail == "차원 불일치 · 등록 1024 · 지금 768"


async def test_check_embedding_passes_when_dimension_matches_registered_model(db_session):
    # 준비
    await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=8)

    # 실행
    result = await embedding.check_embedding(
        db_session, _connection(), transport=embedding_transport(dim=8)
    )

    # 확인
    assert result.ok


async def test_register_model_adds_model_once(db_session):
    # 실행
    first = await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=1024)
    second = await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=1024)

    # 확인
    models = list(await db_session.scalars(select(EmbeddingModel)))
    assert (first, second) == (True, False)
    assert [(model.name, model.dim) for model in models] == [(EMBEDDING_MODEL, 1024)]


# ---------- 계산과 캐시 ----------


async def _registered_model(db_session, *, dim: int = 8) -> EmbeddingModel:
    await embedding.register_model(db_session, name=EMBEDDING_MODEL, dim=dim)
    model = await embedding.get_registered_model(db_session, name=EMBEDDING_MODEL)
    assert model is not None
    return model


async def test_embed_texts_sends_batches_and_keeps_order(monkeypatch):
    # 준비
    monkeypatch.setattr(embedding, "EMBEDDING_BATCH_SIZE", 2)
    transport = vector_transport(dim=4)
    texts = ["하나", "둘", "셋", "넷", "다섯"]

    # 실행
    vectors = await embedding.embed_texts(_connection(), texts, transport=transport)

    # 확인
    assert len(transport.requests) == 3
    assert vectors == [vector_of(text, dim=4) for text in texts]


async def test_embed_texts_retries_when_server_is_busy(monkeypatch):
    # 준비
    monkeypatch.setattr(embedding, "EMBEDDING_RETRY_BASE_S", 0)
    transport = vector_transport(dim=4, fail_first=1)

    # 실행
    vectors = await embedding.embed_texts(_connection(), ["안녕"], transport=transport)

    # 확인
    assert len(transport.requests) == 2
    assert vectors == [vector_of("안녕", dim=4)]


async def test_embed_texts_fails_clearly_after_max_attempts(monkeypatch):
    # 준비
    monkeypatch.setattr(embedding, "EMBEDDING_RETRY_BASE_S", 0)
    transport = vector_transport(dim=4, fail_first=embedding.EMBEDDING_MAX_ATTEMPTS)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        await embedding.embed_texts(_connection(), ["안녕"], transport=transport)

    # 확인
    assert len(transport.requests) == embedding.EMBEDDING_MAX_ATTEMPTS
    assert "HTTP 503" in caught.value.message


async def test_embed_texts_does_not_retry_rejected_request():
    # 준비
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"error": "bad input"})

    # 실행
    with pytest.raises(ExternalServiceError):
        await embedding.embed_texts(_connection(), ["안녕"], transport=httpx.MockTransport(reply))

    # 확인
    assert len(requests) == 1


async def test_save_and_load_vectors_round_trip_in_half_precision(db_session):
    # 준비
    model = await _registered_model(db_session, dim=4)
    vectors = [[0.5, -0.25, 1.0, 0.0], [0.1, 0.2, 0.3, 0.4]]

    # 실행
    await embedding.save_vectors(
        db_session, model=model, text_hashes=["a" * 64, "b" * 64], vectors=vectors
    )
    await db_session.commit()
    loaded = await embedding.load_vectors(db_session, model=model, text_hashes=["b" * 64, "a" * 64])

    # 확인
    assert loaded.dtype == np.float32
    assert loaded.shape == (2, 4)
    np.testing.assert_allclose(loaded, [vectors[1], vectors[0]], atol=1e-3)


async def test_find_missing_hashes_returns_only_uncached_in_order(db_session):
    # 준비
    model = await _registered_model(db_session, dim=4)
    await embedding.save_vectors(
        db_session, model=model, text_hashes=["b" * 64], vectors=[[0.1, 0.2, 0.3, 0.4]]
    )
    await db_session.commit()

    # 실행
    missing = await embedding.find_missing_hashes(
        db_session, model_id=model.id, text_hashes=["c" * 64, "b" * 64, "a" * 64]
    )

    # 확인
    assert missing == ["c" * 64, "a" * 64]


async def test_save_vectors_keeps_first_vector_when_saved_twice(db_session):
    # 준비
    model = await _registered_model(db_session, dim=4)
    await embedding.save_vectors(
        db_session, model=model, text_hashes=["a" * 64], vectors=[[1.0, 0.0, 0.0, 0.0]]
    )

    # 실행
    await embedding.save_vectors(
        db_session, model=model, text_hashes=["a" * 64], vectors=[[0.0, 1.0, 0.0, 0.0]]
    )
    await db_session.commit()

    # 확인
    loaded = await embedding.load_vectors(db_session, model=model, text_hashes=["a" * 64])
    assert loaded.tolist() == [[1.0, 0.0, 0.0, 0.0]]


async def test_save_vectors_fails_when_dimension_differs_from_model(db_session):
    # 준비
    model = await _registered_model(db_session, dim=4)

    # 실행
    with pytest.raises(ExternalServiceError) as caught:
        await embedding.save_vectors(
            db_session, model=model, text_hashes=["a" * 64], vectors=[[0.1, 0.2]]
        )

    # 확인
    assert "등록 4 · 받은 2" in caught.value.message


async def test_load_vectors_fails_when_a_vector_is_missing(db_session):
    # 준비
    model = await _registered_model(db_session, dim=4)

    # 실행 · 확인
    with pytest.raises(ExternalServiceError):
        await embedding.load_vectors(db_session, model=model, text_hashes=["a" * 64])


async def test_ensure_model_registers_unknown_model_from_server(db_session):
    # 실행
    model = await embedding.ensure_model(
        db_session, _connection(), transport=embedding_transport(dim=6)
    )

    # 확인
    assert (model.name, model.dim) == (EMBEDDING_MODEL, 6)


async def test_ensure_model_fails_when_server_is_down(db_session):
    # 실행 · 확인
    with pytest.raises(ExternalServiceError):
        await embedding.ensure_model(db_session, _connection(), transport=refused_transport())
