"""검색 모듈 API 테스트: 데이터셋 · 설정 · 필드 맞춤 짐작 · 진단 · 질의 · 문서 · 판정."""

from fastapi import status
from sqlalchemy import select

from dent.modules.retrieval.models import Document, Query
from dent.system.models import Dataset
from tests.modules.retrieval.helpers import import_csv

API = "/api/v1/retrieval"


async def _query_id(db_session, text: str) -> int:
    return await db_session.scalar(select(Query.id).where(Query.text == text))


async def _document_id(db_session, text: str) -> int:
    return await db_session.scalar(select(Document.id).where(Document.text == text))


# ---------- 데이터셋 · 설정 ----------


async def test_list_datasets_returns_counts_and_entry_stage(client, import_triplets):
    # 준비
    await import_triplets()

    # 실행
    response = await client.get(f"{API}/datasets")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    [summary] = response.json()
    assert summary["name"] == "상담 검색"
    assert summary["query_count"] == 3
    assert summary["document_count"] == 5
    assert summary["positive_count"] == 3
    assert summary["negative_count"] == 4
    assert summary["shape"] == "triplet"
    assert summary["entry_stage"] == 3


async def test_get_dataset_returns_settings(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.get(f"{API}/datasets/{finished.dataset_id}")

    # 확인
    body = response.json()
    assert "split_counts" not in body
    assert body["settings"]["negatives"] == 7
    assert body["settings"]["doc_max_tokens"] == 512


async def test_get_dataset_returns_404_for_other_module_dataset(client, db_session):
    # 준비
    other = Dataset(module="classification", name="분류")
    db_session.add(other)
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/datasets/{other.id}")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_update_settings_changes_only_given_values(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings",
        json={"negatives": 15, "target_model": "bge-m3"},
    )

    # 확인
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["negatives"] == 15
    assert body["target_model"] == "bge-m3"
    assert body["query_max_tokens"] == 64


async def test_update_settings_returns_422_when_rank_range_is_reversed(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings",
        json={"mine_rank_from": 200, "mine_rank_to": 100},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "오답 찾기 순위 범위의 앞이 뒤보다 클 수 없습니다."}


async def test_delete_dataset_removes_queries_and_documents(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.delete(f"{API}/datasets/{finished.dataset_id}")

    # 확인
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert await db_session.scalar(select(Query.id)) is None
    assert await db_session.scalar(select(Document.id)) is None


# ---------- 가져오기 ----------


async def test_suggest_mapping_finds_triplet_shape(client):
    # 실행
    response = await client.post(
        f"{API}/mapping/suggest",
        json={"columns": ["anchor", "positive", "negative_1", "negative_2"], "source": "file"},
    )

    # 확인
    body = response.json()
    assert body["shape"] == "triplet"
    assert body["query"] == "anchor"
    assert body["negatives"] == ["negative_1", "negative_2"]


async def test_suggest_mapping_falls_back_to_documents(client):
    # 실행
    response = await client.post(
        f"{API}/mapping/suggest", json={"columns": ["id", "title", "content"], "source": "file"}
    )

    # 확인
    body = response.json()
    assert body["shape"] == "documents"
    assert (body["text"], body["title"], body["doc_key"]) == ("content", "title", "id")


async def test_create_import_returns_422_when_shape_field_is_missing(client):
    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "new_dataset_name": "빈칸",
            "source": "file",
            "upload_id": "0" * 32,
            "mapping": {"shape": "scored", "query": "q", "document": "d"},
        },
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ---------- 진단 ----------


async def test_overview_for_triplets_starts_at_stage_three(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.get(f"{API}/datasets/{finished.dataset_id}/overview")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["query_count"] == 3
    assert body["flow"]["entry"] == 3
    assert [stage["no"] for stage in body["flow"]["stages"]] == [1, 2, 3]
    assert [move["key"] for move in body["flow"]["moves"]] == ["generate", "mine", "export"]
    checks = {check["key"]: check for check in body["checks"]}
    # 오답이 7개보다 적은 질의 3개
    assert checks["no_negative"]["view_count"] == 3
    assert checks["no_positive"]["grade"] == "good"
    assert body["kpi"] is None


async def test_overview_for_documents_only_asks_to_generate_queries(client, db_session):
    # 준비
    finished = await import_csv(
        db_session,
        header=["text"],
        rows=[["환불은 영업일 기준 3일 안에 처리됩니다."], ["배송은 이틀 걸립니다."]],
        mapping={"shape": "documents", "text": "text"},
    )

    # 실행
    response = await client.get(f"{API}/datasets/{finished.dataset_id}/overview")

    # 확인
    flow = response.json()["flow"]
    assert flow["entry"] == 1
    generate = next(move for move in flow["moves"] if move["key"] == "generate")
    assert generate["state"] == "bad"
    assert flow["current"] in (1, "generate")


async def test_overview_merges_source_splits_and_marks_conflict(client, db_session):
    # 준비: 같은 질의가 원본의 train · test에 있다(분할 열은 쓰지 않는다).
    finished = await import_csv(
        db_session,
        header=["query", "doc", "score", "split"],
        rows=[
            ["환불 기간", "환불은 3일", "1", "train"],
            ["환불 기간", "환불은 3일", "0", "train"],
            ["환불 기간", "환불은 3일", "1", "test"],
        ],
        mapping={
            "shape": "scored",
            "query": "query",
            "document": "doc",
            "score": "score",
        },
    )

    # 실행
    response = await client.get(f"{API}/datasets/{finished.dataset_id}/overview")

    # 확인: 분할이 달라도 같은 질의는 하나로 합치고, 정답이자 오답인 판정은 충돌이다.
    body = response.json()
    checks = {check["key"]: check for check in body["checks"]}
    assert body["query_count"] == 1
    assert checks["conflict"]["view_count"] == 1
    assert not {"leak", "doc_leak"} & set(checks)


async def test_same_negative_counts_only_own_positive(client, db_session):
    # 준비: 둘째 질의의 오답이 첫째 질의의 정답과 같은 글이다(다른 질의의 정답이라 문제가 아니다).
    finished = await import_csv(
        db_session,
        header=["query", "positive", "negative"],
        rows=[
            ["환불 기간", "환불은 3일", "배송은 2일"],
            ["배송 기간", "배송은 2일", "환불은 3일"],
            ["회원 가입", "가입은 무료", "가입은 무료"],
        ],
        mapping={
            "shape": "triplet",
            "query": "query",
            "positive": "positive",
            "negatives": ["negative"],
        },
    )

    # 실행
    response = await client.get(
        f"{API}/datasets/{finished.dataset_id}/queries", params={"problem": "same_negative"}
    )

    # 확인
    assert [item["text"] for item in response.json()["items"]] == []


# ---------- 질의 ----------


async def test_list_queries_filters_by_search(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    searched = await client.get(
        f"{API}/datasets/{finished.dataset_id}/queries", params={"q": "배송"}
    )

    # 확인
    body = searched.json()
    assert body["total"] == 1
    [item] = body["items"]
    assert item["positive_count"] == 1
    assert item["negative_count"] == 1
    assert item["positives"][0]["snippet"].startswith("배송비는")
    assert "no_negative" in item["marks"]


async def test_list_queries_filters_by_problem(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.get(
        f"{API}/datasets/{finished.dataset_id}/queries", params={"problem": "no_positive"}
    )

    # 확인
    assert response.json()["total"] == 0


async def test_get_query_lists_judgments_without_ranking(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "환불은 며칠 걸리나요")

    # 실행
    response = await client.get(f"{API}/queries/{query_id}")

    # 확인
    body = response.json()
    assert body["has_ranking"] is False
    grades = sorted(item["grade"] for item in body["ranked"])
    assert grades == [0, 0, 1]


async def test_get_query_returns_404_when_missing(client):
    # 실행
    response = await client.get(f"{API}/queries/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "질의를 찾을 수 없습니다."}


async def test_update_query_changes_text_and_source(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "환불은 며칠 걸리나요")

    # 실행
    response = await client.patch(
        f"{API}/queries/{query_id}", json={"text": "환불 기간은?", "row_version": 1}
    )

    # 확인
    assert response.status_code == status.HTTP_200_OK
    query = response.json()["query"]
    assert query["text"] == "환불 기간은?"
    assert query["source"] == "human"
    assert query["row_version"] == 2


async def test_update_query_returns_409_when_row_version_is_old(
    client, db_session, import_triplets
):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "환불은 며칠 걸리나요")
    await client.patch(f"{API}/queries/{query_id}", json={"excluded": True, "row_version": 1})

    # 실행
    response = await client.patch(
        f"{API}/queries/{query_id}", json={"trashed": True, "row_version": 1}
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "그 사이 다른 곳에서 고쳤습니다. 다시 불러온 뒤 고치세요."}


async def test_bulk_trash_and_restore_queries(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()
    ids = list((await db_session.scalars(select(Query.id).order_by(Query.id))).all())

    # 실행
    trashed = await client.post(
        f"{API}/datasets/{finished.dataset_id}/queries/bulk",
        json={"query_ids": ids[:2], "action": "trash"},
    )
    trash_page = await client.get(
        f"{API}/datasets/{finished.dataset_id}/queries", params={"status": "trash"}
    )
    restored = await client.post(
        f"{API}/datasets/{finished.dataset_id}/queries/bulk",
        json={"query_ids": ids, "action": "restore"},
    )

    # 확인
    assert trashed.json() == {"changed": 2}
    assert trash_page.json()["total"] == 2
    assert restored.json() == {"changed": 2}


# ---------- 문서 ----------


async def test_list_documents_filters_by_use(client, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    positive = await client.get(
        f"{API}/datasets/{finished.dataset_id}/documents", params={"use": "positive"}
    )
    negative = await client.get(
        f"{API}/datasets/{finished.dataset_id}/documents", params={"use": "negative"}
    )

    # 확인
    assert positive.json()["total"] == 3
    # 배송 · 회원 가입만 오답으로만 쓴다. 환불 문서는 정답이면서 오답이기도 하다.
    assert {item["text"] for item in negative.json()["items"]} >= {
        "배송은 이틀 걸립니다.",
        "회원 가입은 무료입니다.",
    }


async def test_get_document_lists_queries_using_it(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    document_id = await _document_id(db_session, "환불은 영업일 기준 3일 안에 처리됩니다.")

    # 실행
    response = await client.get(f"{API}/documents/{document_id}")

    # 확인
    body = response.json()
    assert body["document"]["positive_count"] == 1
    assert body["document"]["negative_count"] == 1
    assert sorted(item["grade"] for item in body["uses"]) == [0, 1]


async def test_update_document_recounts_tokens(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    document_id = await _document_id(db_session, "회원 가입은 무료입니다.")

    # 실행
    response = await client.patch(
        f"{API}/documents/{document_id}",
        json={"text": "회원 가입은 무료이고 탈퇴도 언제든 할 수 있습니다.", "row_version": 1},
    )

    # 확인
    document = response.json()["document"]
    assert document["text"].startswith("회원 가입은 무료이고")
    assert document["token_count"] > 0
    assert document["row_version"] == 2


async def test_bulk_skip_generation_on_documents(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()
    ids = list((await db_session.scalars(select(Document.id))).all())

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/documents/bulk",
        json={"document_ids": ids, "action": "skip_generation"},
    )

    # 확인
    assert response.json() == {"changed": 5}


# ---------- 판정 ----------


async def test_set_judgment_adds_human_positive(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "비밀번호를 잊었어요")
    document_id = await _document_id(db_session, "회원 가입은 무료입니다.")

    # 실행
    response = await client.put(
        f"{API}/queries/{query_id}/judgments/{document_id}", json={"grade": 2}
    )

    # 확인
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert (body["grade"], body["source"], body["conflict"]) == (2, "human", False)


async def test_set_judgment_returns_422_when_grade_is_out_of_range(
    client, db_session, import_triplets
):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "비밀번호를 잊었어요")
    document_id = await _document_id(db_session, "회원 가입은 무료입니다.")

    # 실행
    response = await client.put(
        f"{API}/queries/{query_id}/judgments/{document_id}", json={"grade": 4}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_set_judgment_returns_422_for_other_dataset_document(
    client, db_session, import_triplets
):
    # 준비
    await import_triplets("첫째")
    await import_csv(
        db_session,
        header=["text"],
        rows=[["다른 데이터셋의 문서"]],
        mapping={"shape": "documents", "text": "text"},
        name="둘째",
    )
    query_id = await _query_id(db_session, "비밀번호를 잊었어요")
    document_id = await _document_id(db_session, "다른 데이터셋의 문서")

    # 실행
    response = await client.put(
        f"{API}/queries/{query_id}/judgments/{document_id}", json={"grade": 1}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "같은 데이터셋의 질의 · 문서가 아닙니다."}


async def test_delete_judgment_returns_404_when_missing(client, db_session, import_triplets):
    # 준비
    await import_triplets()
    query_id = await _query_id(db_session, "비밀번호를 잊었어요")
    document_id = await _document_id(db_session, "회원 가입은 무료입니다.")

    # 실행
    response = await client.delete(f"{API}/queries/{query_id}/judgments/{document_id}")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
