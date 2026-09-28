"""문서 나누기 · 되돌리기, 휴지통 비우기 테스트."""

import itertools

from fastapi import status
from sqlalchemy import func, select

from dent.modules.retrieval.models import Document, Judgment, Query
from tests.modules.retrieval.helpers import import_csv

API = "/api/v1/retrieval"

# 512토큰을 넘는 긴 문서: 문단 여럿. 둘째 문단에 답(“서울”)이 있다.
PARAGRAPHS = [
    "제1조 목적. " + "이 규정은 회사의 휴가 제도를 정한다. " * 40,
    "제2조 근무지. 본사는 서울에 있다. " + "근무지는 인사 발령에 따른다. " * 40,
    "제3조 기타. " + "정하지 않은 것은 따로 정한다. " * 40,
]
LONG_TEXT = "\n\n".join(PARAGRAPHS)


async def _long_dataset(db_session) -> tuple[int, int, int]:
    finished = await import_csv(
        db_session,
        header=["question", "context", "answers"],
        rows=[["본사는 어디에 있나", LONG_TEXT, "서울"]],
        mapping={"shape": "mrc", "query": "question", "positive": "context", "answer": "answers"},
    )
    document_id = await db_session.scalar(select(Document.id))
    query_id = await db_session.scalar(select(Query.id))
    return finished.dataset_id, document_id, query_id


async def test_split_document_moves_judgment_to_piece_with_answer(client, db_session):
    # 준비
    dataset_id, document_id, query_id = await _long_dataset(db_session)

    # 실행
    response = await client.post(f"{API}/documents/{document_id}/split")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["changed"] >= 2
    original = await db_session.get(Document, document_id, populate_existing=True)
    assert original.replaced_at is not None
    positive_piece = await db_session.scalar(
        select(Document)
        .join(Judgment, Judgment.document_id == Document.id)
        .where(Judgment.query_id == query_id, Document.source_document_id == document_id)
    )
    assert "서울" in positive_piece.text
    listing = await client.get(f"{API}/datasets/{dataset_id}/documents")
    assert listing.json()["total"] == response.json()["changed"]


async def test_split_document_overlaps_chunks_by_setting(client, db_session):
    # 준비: 번호가 붙은 문장으로 된 긴 문서 하나 · 오버랩 64토큰
    text = "\n".join(f"{index}번째 문장은 휴가 규정의 한 줄이다." for index in range(120))
    finished = await import_csv(
        db_session,
        header=["text"],
        rows=[[text]],
        mapping={"shape": "documents", "text": "text"},
    )
    document_id = await db_session.scalar(select(Document.id))
    await client.patch(f"{API}/datasets/{finished.dataset_id}/settings", json={"chunk_overlap": 64})

    # 실행
    await client.post(f"{API}/documents/{document_id}/split")

    # 확인: 다음 청크는 앞 청크의 끝 문장(들)로 시작한다.
    pieces = list(
        await db_session.scalars(
            select(Document)
            .where(Document.source_document_id == document_id)
            .order_by(Document.chunk_index)
        )
    )
    assert len(pieces) >= 2
    for before, after in itertools.pairwise(pieces):
        before_lines, after_lines = before.text.split("\n"), after.text.split("\n")
        # 겹친 곳: 뒤 청크의 첫 줄은 앞 청크에 있고, 앞 청크의 끝 줄은 뒤 청크에 있다.
        assert after_lines[0] in before_lines
        assert before_lines[-1] in after_lines


async def test_unsplit_document_restores_original(client, db_session):
    # 준비
    dataset_id, document_id, _query_id = await _long_dataset(db_session)
    await client.post(f"{API}/documents/{document_id}/split")

    # 실행
    response = await client.post(f"{API}/documents/{document_id}/unsplit")

    # 확인
    assert response.json()["changed"] >= 2
    listing = await client.get(f"{API}/datasets/{dataset_id}/documents")
    assert [item["id"] for item in listing.json()["items"]] == [document_id]


async def test_split_document_returns_422_when_short(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    document_id = await db_session.scalar(select(Document.id).order_by(Document.id))

    # 실행
    response = await client.post(f"{API}/documents/{document_id}/split")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "나눌 만큼 긴 문서가 아닙니다."}


async def test_empty_trash_deletes_trashed_queries(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()
    ids = list((await db_session.scalars(select(Query.id).order_by(Query.id))).all())
    await client.post(
        f"{API}/datasets/{finished.dataset_id}/queries/bulk",
        json={"query_ids": ids[:1], "action": "trash"},
    )

    # 실행
    response = await client.delete(
        f"{API}/datasets/{finished.dataset_id}/trash", params={"kind": "queries"}
    )

    # 확인
    assert response.json() == {"changed": 1}
    assert await db_session.scalar(select(func.count()).select_from(Query)) == 2
