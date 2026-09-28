"""반복 구간 테스트: 가져온 뒤 살피기 · 떼기로 고르면 학습 글만 바뀜 · 다시 살펴도 결정이 이어짐 · 진단 · 문제 거르기 · API."""

from fastapi import status
from sqlalchemy import select

from dent.modules.retrieval.jobs import run_scan_job
from dent.modules.retrieval.models import Document, Repeat, RepeatKind
from dent.system import jobs
from dent.system.models import JobStatus
from tests.modules.retrieval.helpers import import_csv

API = "/api/v1/retrieval"

# 여섯 문서 모두 끝에 같은 틀의 문장(날짜만 다름)이 있고, 넷에는 이메일이 있다.
CLOSING = "이 안내는 2024년 {month}월 1일부터 적용한다."
BODIES = [
    "휴가는 연 15일이며 입사 첫해에는 달마다 하루씩 생긴다.",
    "출장비는 교통비와 숙박비를 실제 쓴 만큼 돌려준다.",
    "재택근무는 주 2일까지 팀장 승인을 받아 쓸 수 있다.",
    "교육비는 한 해 100만 원까지 회사가 낸다.",
    "경조사 휴가는 결혼 5일, 출산 10일을 준다.",
    "보안 교육은 한 해 한 번 꼭 들어야 한다.",
]


def _text(index: int) -> str:
    contact = " 문의 hr@example.com" if index < 4 else ""
    return f"{BODIES[index]}{contact}\n{CLOSING.format(month=index + 1)}"


async def _dataset(db_session) -> int:
    finished = await import_csv(
        db_session,
        header=["title", "text"],
        rows=[[f"규정 {index + 1}", _text(index)] for index in range(len(BODIES))],
        mapping={"shape": "documents", "text": "text", "title": "title"},
    )
    return finished.dataset_id


async def _repeat(db_session, kind: RepeatKind) -> Repeat:
    """그 종류에서 가장 많이 든 반복 구간."""
    return await db_session.scalar(
        select(Repeat)
        .where(Repeat.kind == kind.value)
        .order_by(Repeat.document_count.desc(), Repeat.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def test_import_scans_repeated_sentences_and_meta_shapes(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/repeats")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    found = {(item["kind"], item["document_count"], item["suggestion"]) for item in body["items"]}
    # 끝 문장(날짜만 다름) 여섯 · 이메일이 든 문장 넷 · 이메일 모양 넷
    assert found == {("sentence", 6, "keep"), ("sentence", 4, "remove"), ("meta", 4, "remove")}
    assert body["counts"] == {"remove": 0, "keep": 0, "undecided": 3}
    assert body["scanned_at"] is not None


async def test_training_text_has_title_before_body(db_session):
    # 준비
    await _dataset(db_session)

    # 실행
    document = await db_session.scalar(select(Document).order_by(Document.id))

    # 확인
    assert document.training_text == f"규정 1\n{_text(0)}"


async def test_decide_remove_changes_training_text_but_keeps_body(client, db_session):
    # 준비
    await _dataset(db_session)
    meta = await _repeat(db_session, RepeatKind.META)
    before = await db_session.scalar(select(Document).order_by(Document.id))
    old_hash = before.input_hash

    # 실행
    response = await client.patch(f"{API}/repeats/{meta.id}", json={"decision": "remove"})

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["decision"] == "remove"
    document = await db_session.get(Document, before.id, populate_existing=True)
    assert "hr@example.com" in document.text
    assert "hr@example.com" not in document.training_text
    assert document.input_hash != old_hash


async def test_decide_none_restores_training_text(client, db_session):
    # 준비
    await _dataset(db_session)
    meta = await _repeat(db_session, RepeatKind.META)
    await client.patch(f"{API}/repeats/{meta.id}", json={"decision": "remove"})

    # 실행
    await client.patch(f"{API}/repeats/{meta.id}", json={"decision": None})

    # 확인
    document = await db_session.scalar(
        select(Document).order_by(Document.id).execution_options(populate_existing=True)
    )
    assert "hr@example.com" in document.training_text


async def test_rescan_keeps_decisions(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    sentence = await _repeat(db_session, RepeatKind.SENTENCE)
    await client.patch(f"{API}/repeats/{sentence.id}", json={"decision": "keep"})

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/repeats/scan")
    job = await jobs.get_job(db_session, job_id=response.json()["job_id"])
    await run_scan_job(job)

    # 확인
    assert response.status_code == status.HTTP_201_CREATED
    await db_session.refresh(job)
    assert job.status == JobStatus.DONE
    again = await _repeat(db_session, RepeatKind.SENTENCE)
    assert again.decision == "keep"


async def test_overview_repeat_check_warns_until_all_decided(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    before = await client.get(f"{API}/datasets/{dataset_id}/overview")
    for item in (await client.get(f"{API}/datasets/{dataset_id}/repeats")).json()["items"]:
        await client.patch(f"{API}/repeats/{item['id']}", json={"decision": item["suggestion"]})
    after = await client.get(f"{API}/datasets/{dataset_id}/overview")

    # 확인
    check_before = next(c for c in before.json()["checks"] if c["key"] == "repeat")
    check_after = next(c for c in after.json()["checks"] if c["key"] == "repeat")
    assert check_before["grade"] == "warn"
    assert check_before["view_count"] == 6
    assert check_after["grade"] == "good"


async def test_documents_filter_by_repeat_lists_undecided_documents(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    sentence = await _repeat(db_session, RepeatKind.SENTENCE)
    await client.patch(f"{API}/repeats/{sentence.id}", json={"decision": "keep"})

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/documents?problem=repeat")

    # 확인: 남은 고를 것은 이메일(메타 모양)뿐이라 이메일이 든 네 문서만
    assert response.json()["total"] == 4


async def test_decide_repeat_returns_404_when_missing(client, db_session):
    # 실행
    response = await client.patch(f"{API}/repeats/999", json={"decision": "keep"})

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "반복 구간을 찾을 수 없습니다."}


async def test_decide_repeat_returns_422_for_unknown_decision(client, db_session):
    # 준비
    await _dataset(db_session)
    meta = await _repeat(db_session, RepeatKind.META)

    # 실행
    response = await client.patch(f"{API}/repeats/{meta.id}", json={"decision": "delete"})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_section_header_setting_rebuilds_training_text(client, db_session):
    # 준비: 조 하나가 여러 청크로 나뉠 만큼 긴 문서를 나눈다.
    parts = [
        f"제{index}조(항목 {index})\n" + "이 조는 여러 번 되풀이되는 긴 설명을 담고 있다.\n" * 60
        for index in range(1, 3)
    ]
    finished = await import_csv(
        db_session,
        header=["title", "text"],
        rows=[["긴 규정", "\n".join(parts)]],
        mapping={"shape": "documents", "text": "text", "title": "title"},
    )
    original = await db_session.scalar(select(Document.id))
    await client.post(f"{API}/documents/{original}/split")
    # 조의 둘째 청크: 본문이 조 제목으로 시작하지 않아 머리말에 구획이 붙는다.
    piece = await db_session.scalar(
        select(Document)
        .where(Document.section == "제1조(항목 1)", Document.chunk_index > 0)
        .order_by(Document.chunk_index)
        .limit(1)
    )
    first_line = piece.training_text.splitlines()[0]

    # 실행
    response = await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings", json={"section_header": False}
    )

    # 확인
    assert response.json()["section_header"] is False
    assert first_line == "긴 규정 › 제1조(항목 1)"
    refreshed = await db_session.get(Document, piece.id, populate_existing=True)
    assert refreshed.training_text.splitlines()[0] == "긴 규정"


async def test_bulk_decide_follows_suggestions(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    items = (await client.get(f"{API}/datasets/{dataset_id}/repeats")).json()["items"]

    # 실행
    response = await client.post(
        f"{API}/datasets/{dataset_id}/repeats/bulk",
        json={"repeat_ids": [item["id"] for item in items], "follow_suggestion": True},
    )

    # 확인: 메타 모양 · 이메일이 든 문장은 떼기, 끝 문장은 남김
    assert response.json() == {"changed": 3}
    body = (await client.get(f"{API}/datasets/{dataset_id}/repeats")).json()
    assert body["counts"] == {"remove": 2, "keep": 1, "undecided": 0}
    document = await db_session.scalar(
        select(Document).order_by(Document.id).execution_options(populate_existing=True)
    )
    assert "hr@example.com" not in document.training_text
    assert CLOSING.format(month=1) in document.training_text


async def test_bulk_decide_keep_leaves_training_text(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    sentence = await _repeat(db_session, RepeatKind.SENTENCE)
    before = await db_session.scalar(select(Document.input_hash).order_by(Document.id))

    # 실행
    response = await client.post(
        f"{API}/datasets/{dataset_id}/repeats/bulk",
        json={"repeat_ids": [sentence.id], "decision": "keep"},
    )

    # 확인: 남김은 학습 글을 바꾸지 않는다.
    assert response.json() == {"changed": 1}
    after = await db_session.scalar(
        select(Document.input_hash).order_by(Document.id).execution_options(populate_existing=True)
    )
    assert after == before


async def test_bulk_decide_returns_404_for_missing_dataset(client, db_session):
    # 실행
    response = await client.post(
        f"{API}/datasets/999/repeats/bulk", json={"repeat_ids": [1], "decision": "keep"}
    )

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_bulk_decide_returns_422_without_ids(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    response = await client.post(
        f"{API}/datasets/{dataset_id}/repeats/bulk", json={"repeat_ids": [], "decision": "keep"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_repeat_samples_show_span_with_context(client, db_session):
    # 준비
    await _dataset(db_session)
    meta = await _repeat(db_session, RepeatKind.META)

    # 실행
    response = await client.get(f"{API}/repeats/{meta.id}/samples")

    # 확인
    samples = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert len(samples) == 3
    assert samples[0]["title"] == "규정 1"
    assert samples[0]["span"] == "hr@example.com"
    assert samples[0]["before"].endswith("문의 ")
    assert samples[0]["after"].startswith("\n이 안내는")


async def test_repeat_samples_return_404_when_missing(client, db_session):
    # 실행
    response = await client.get(f"{API}/repeats/999/samples")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
