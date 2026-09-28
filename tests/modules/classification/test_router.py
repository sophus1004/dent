"""분류 API 테스트: 주소, 상태 코드, 응답 모양, 오류 문장. 의미 지도는 가짜 임베딩 서버로 끝까지 돌린다.

파일 올리기·허깅페이스 미리 보기·데이터셋 이름 고치기·가져오기 기록은 시스템 API 테스트(tests/api)가 본다.
"""

import httpx
from fastapi import status
from sqlalchemy import func, select

from dent.modules.classification.models import Label, Map, MapPoint, Record
from dent.system import datasets as datasets_service
from dent.system.models import Embedding, Import, ImportSource, ImportStatus, Job
from tests.modules.classification.helpers import Row, connect_embedding, run_map
from tests.system.helpers import vector_transport

API = "/api/v1/classification"
SYSTEM_API = "/api/v1"


async def _upload(client, file_name: str, content: bytes) -> httpx.Response:
    """파일 하나를 시스템 API로 올린다(multipart)."""
    return await client.post(f"{SYSTEM_API}/uploads", files={"file": (file_name, content)})


# ---------- 데이터셋 ----------


async def test_list_datasets_returns_recently_changed_first(client, make_dataset):
    # 준비
    older = await make_dataset("먼저 만든 것", [Row("안녕", "인사")])
    newer = await make_dataset("나중에 만든 것", [])
    await client.post(f"{API}/datasets/{older.dataset.id}/labels", json={"name": "새 라벨"})

    # 실행
    response = await client.get(f"{API}/datasets")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert [item["id"] for item in body] == [older.dataset.id, newer.dataset.id]
    assert body[0]["record_count"] == 1
    assert body[0]["label_count"] == 2
    assert body[0]["importing"] is False


async def test_get_dataset_returns_labels(client, make_dataset):
    # 준비
    made = await make_dataset("자세히", [Row("안녕", "인사"), Row("환불요", "환불")])

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert body["name"] == "자세히"
    assert [(label["name"], label["record_count"]) for label in body["labels"]] == [
        ("인사", 1),
        ("환불", 1),
    ]
    assert "split_counts" not in body


async def test_update_settings_saves_balance_target(client, make_dataset):
    # 준비
    made = await make_dataset("설정", [Row("안녕", "인사")])
    url = f"{API}/datasets/{made.dataset.id}"
    before = (await client.get(url)).json()

    # 실행
    response = await client.patch(f"{url}/settings", json={"balance_target": 2.0})
    after = (await client.get(url)).json()

    # 확인: 처음엔 기본값 1.5. 실행 설정은 데이터셋 수정 시각을 건드리지 않는다.
    assert before["settings"] == {"balance_target": 1.5}
    assert response.status_code == status.HTTP_200_OK
    assert after["settings"] == {"balance_target": 2.0}
    assert after["updated_at"] == before["updated_at"]


async def test_update_settings_returns_422_when_target_is_out_of_range(client, make_dataset):
    # 준비
    made = await make_dataset("설정", [Row("안녕", "인사")])

    # 실행
    response = await client.patch(
        f"{API}/datasets/{made.dataset.id}/settings", json={"balance_target": 5}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_update_settings_returns_404_when_dataset_is_missing(client):
    # 실행
    response = await client.patch(f"{API}/datasets/999/settings", json={"balance_target": 2})

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_get_dataset_returns_404_when_missing(client):
    # 실행
    response = await client.get(f"{API}/datasets/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "데이터셋을 찾을 수 없습니다."}


async def test_list_datasets_leaves_out_datasets_of_other_modules(client, db_session, make_dataset):
    # 준비
    mine = await make_dataset("분류 데이터", [])
    await datasets_service.create_dataset(db_session, module="generation", name="생성 데이터")
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/datasets")

    # 확인
    assert [item["id"] for item in response.json()] == [mine.dataset.id]


async def test_get_dataset_returns_404_when_dataset_belongs_to_other_module(client, db_session):
    # 준비
    other = await datasets_service.create_dataset(db_session, module="generation", name="대화")
    await db_session.commit()

    # 실행
    response = await client.get(f"{API}/datasets/{other.id}")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "데이터셋을 찾을 수 없습니다."}


async def _add_import(db_session, *, dataset_id: int, import_status: ImportStatus) -> None:
    """데이터셋에 가져오기 기록 한 줄을 바로 넣는다."""
    db_session.add(
        Import(
            dataset_id=dataset_id,
            source=ImportSource.FILE,
            source_name="뉴스.csv",
            options={},
            status=import_status,
        )
    )
    await db_session.commit()


async def _count(db_session, model, *, dataset_id: int) -> int:
    return await db_session.scalar(
        select(func.count()).select_from(model).where(model.dataset_id == dataset_id)
    )


async def test_delete_dataset_returns_204_and_removes_everything_inside(
    client, db_session, make_dataset
):
    # 준비: 지울 데이터셋에는 문장 · 라벨 · 가져오기 기록 · 다 만든 의미 지도가 있다.
    made = await make_dataset("지울 것", [Row("안녕", "인사"), Row("환불요", "환불")])
    kept = await make_dataset("남길 것", [Row("배송은요", "배송")])
    await _add_import(db_session, dataset_id=made.dataset.id, import_status=ImportStatus.DONE)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")
    await run_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())
    points_before = await db_session.scalar(select(func.count()).select_from(MapPoint))
    assert points_before == 2
    embeddings_before = await db_session.scalar(select(func.count()).select_from(Embedding))

    # 실행
    response = await client.delete(f"{API}/datasets/{made.dataset.id}")

    # 확인
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""
    missing = await client.get(f"{API}/datasets/{made.dataset.id}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    for model in (Record, Label, Map, Import):
        assert await _count(db_session, model, dataset_id=made.dataset.id) == 0
    point_count = await db_session.scalar(select(func.count()).select_from(MapPoint))
    assert point_count == 0
    assert await _count(db_session, Record, dataset_id=kept.dataset.id) == 1
    embeddings_after = await db_session.scalar(select(func.count()).select_from(Embedding))
    assert embeddings_after == embeddings_before > 0


async def test_delete_dataset_returns_404_when_missing(client):
    # 실행
    response = await client.delete(f"{API}/datasets/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "데이터셋을 찾을 수 없습니다."}


async def test_delete_dataset_returns_404_when_dataset_belongs_to_other_module(client, db_session):
    # 준비
    other = await datasets_service.create_dataset(db_session, module="generation", name="대화")
    await db_session.commit()

    # 실행
    response = await client.delete(f"{API}/datasets/{other.id}")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert await datasets_service.get_dataset(db_session, dataset_id=other.id) is not None


async def test_delete_dataset_returns_409_when_map_is_building(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("지도 만드는 중", [Row("안녕", "인사")])
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 실행
    response = await client.delete(f"{API}/datasets/{made.dataset.id}")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "의미 지도를 만드는 중인 데이터셋은 지울 수 없습니다. 지도 만들기를 멈춘 뒤 지우세요."
    }
    assert (await client.get(f"{API}/datasets/{made.dataset.id}")).status_code == status.HTTP_200_OK


async def test_delete_dataset_returns_409_when_import_is_running(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("가져오는 중", [Row("안녕", "인사")])
    await _add_import(db_session, dataset_id=made.dataset.id, import_status=ImportStatus.RUNNING)

    # 실행
    response = await client.delete(f"{API}/datasets/{made.dataset.id}")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "가져오는 중인 데이터셋은 지울 수 없습니다. 가져오기가 끝난 뒤 지우세요."
    }
    assert await _count(db_session, Record, dataset_id=made.dataset.id) == 1


# ---------- 라벨 ----------


async def test_create_label_returns_201_with_zero_records(client, make_dataset):
    # 준비
    made = await make_dataset("라벨", [])

    # 실행
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/labels", json={"name": "환불", "description": "돈"}
    )

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_201_CREATED
    assert (body["name"], body["description"], body["record_count"]) == ("환불", "돈", 0)
    assert body["dataset_id"] == made.dataset.id


async def test_create_label_returns_409_when_name_is_taken(client, make_dataset):
    # 준비
    made = await make_dataset("라벨", [Row("안녕", "인사")])

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/labels", json={"name": "인사"})

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "같은 이름의 라벨이 이미 있습니다."}


async def test_create_label_returns_422_when_name_is_blank(client, make_dataset):
    # 준비
    made = await make_dataset("라벨", [])

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/labels", json={"name": "   "})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert isinstance(response.json()["detail"], list)


async def test_update_label_keeps_record_count(client, make_dataset):
    # 준비
    made = await make_dataset("라벨", [Row("안녕", "인사"), Row("반가워", "인사")])

    # 실행
    response = await client.patch(f"{API}/labels/{made.labels['인사'].id}", json={"name": "인사말"})

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert (response.json()["name"], response.json()["record_count"]) == ("인사말", 2)


# ---------- 필드 맞춤 짐작과 가져오기 ----------


async def test_suggest_mapping_picks_text_and_label_columns(client):
    # 실행
    response = await client.post(
        f"{API}/mapping/suggest", json={"columns": ["id", "Sentence", "의도", "split"]}
    )

    # 확인
    # 분할 열이 있어도 고르지 않는다(원본 분할은 하나로 합친다).
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"text": "Sentence", "label": "의도"}


async def test_create_import_queues_job_and_new_dataset(client, db_session):
    # 준비
    uploaded = (await _upload(client, "인사.csv", "text,label\n안녕,인사\n".encode())).json()

    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "new_dataset_name": "새 데이터",
            "source": "file",
            "upload_id": uploaded["upload_id"],
            "mapping": {"text": "text", "label": "label"},
        },
    )

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_201_CREATED
    assert (body["status"], body["source"], body["source_name"]) == ("queued", "file", "인사.csv")
    job = await db_session.scalar(select(Job).where(Job.id == body["job_id"]))
    assert job is not None
    assert (job.module, job.kind, job.params) == (
        "classification",
        "import",
        {"import_id": body["id"]},
    )
    dataset = (await client.get(f"{API}/datasets/{body['dataset_id']}")).json()
    assert (dataset["name"], dataset["importing"]) == ("새 데이터", True)
    registry = (await client.get(f"{SYSTEM_API}/datasets")).json()
    assert [(item["id"], item["module"]) for item in registry] == [
        (body["dataset_id"], "classification")
    ]
    imports = (await client.get(f"{SYSTEM_API}/datasets/{body['dataset_id']}/imports")).json()
    assert [item["id"] for item in imports] == [body["id"]]
    fetched = await client.get(f"{SYSTEM_API}/imports/{body['id']}")
    assert fetched.json()["job_id"] == body["job_id"]


async def test_create_import_returns_409_when_new_name_is_taken(client, make_dataset):
    # 준비
    await make_dataset("있는 이름", [])
    uploaded = (await _upload(client, "인사.csv", "text,label\n안녕,인사\n".encode())).json()

    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "new_dataset_name": "있는 이름",
            "source": "file",
            "upload_id": uploaded["upload_id"],
            "mapping": {"text": "text", "label": "label"},
        },
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT


async def test_create_import_returns_422_when_both_dataset_and_new_name_are_given(
    client, make_dataset
):
    # 준비
    made = await make_dataset("있는 것", [])

    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "dataset_id": made.dataset.id,
            "new_dataset_name": "새 것",
            "source": "huggingface",
            "repo": "someone/data",
            "mapping": {"text": "text", "label": "label"},
        },
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "하나만" in response.json()["detail"]


async def test_create_import_returns_404_when_upload_is_missing(client):
    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "new_dataset_name": "새 데이터",
            "source": "file",
            "upload_id": "b" * 32,
            "mapping": {"text": "text", "label": "label"},
        },
    )

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "올린 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요."}


# ---------- 문장 ----------


async def test_list_records_returns_page_with_label_names(client, make_dataset):
    # 준비
    made = await make_dataset(
        "문장", [Row("안녕", "인사"), Row("안녕", "환불"), Row("휴지통", "인사", trashed=True)]
    )

    # 실행
    response = await client.get(
        f"{API}/datasets/{made.dataset.id}/records", params={"problem": "conflict", "limit": 1}
    )

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["total"], body["limit"], body["offset"]) == (2, 1, 0)
    item = body["items"][0]
    assert (item["text"], item["label_name"]) == ("안녕", "인사")
    assert "split" not in item
    assert (item["duplicate_count"], item["has_conflict"], item["is_trashed"]) == (1, True, False)
    assert (item["exclude_reason"], item["row_version"], item["extra"]) == (None, 1, {})


async def test_list_records_returns_trash_with_status_filter(client, make_dataset):
    # 준비
    made = await make_dataset("문장", [Row("안녕", "인사"), Row("휴지통", "인사", trashed=True)])

    # 실행
    response = await client.get(
        f"{API}/datasets/{made.dataset.id}/records", params={"status": "trash"}
    )

    # 확인
    items = response.json()["items"]
    assert [(item["text"], item["is_trashed"]) for item in items] == [("휴지통", True)]
    assert items[0]["trashed_at"] is not None


async def test_list_records_returns_422_when_limit_is_too_big(client, make_dataset):
    # 준비
    made = await make_dataset("문장", [])

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/records", params={"limit": 201})

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_update_record_returns_409_when_row_version_is_stale(client, make_dataset):
    # 준비
    made = await make_dataset("고치기", [Row("안녕", "인사")])
    record_id = made.records[0].id
    first = await client.patch(
        f"{API}/records/{record_id}", json={"text": "안녕하세요", "row_version": 1}
    )

    # 실행
    second = await client.patch(
        f"{API}/records/{record_id}", json={"text": "반가워요", "row_version": 1}
    )

    # 확인
    assert first.status_code == status.HTTP_200_OK
    assert (first.json()["text"], first.json()["row_version"]) == ("안녕하세요", 2)
    assert second.status_code == status.HTTP_409_CONFLICT
    assert second.json() == {"detail": "다른 곳에서 먼저 고쳤습니다. 새로 불러오세요."}


async def test_update_record_returns_422_when_label_is_from_other_dataset(client, make_dataset):
    # 준비
    mine = await make_dataset("내 것", [Row("안녕", "인사")])
    other = await make_dataset("남의 것", [Row("환불요", "환불")])

    # 실행
    response = await client.patch(
        f"{API}/records/{mine.records[0].id}",
        json={"label_id": other.labels["환불"].id, "row_version": 1},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "이 데이터셋의 라벨이 아닙니다."}


async def test_update_record_returns_404_when_missing(client):
    # 실행
    response = await client.patch(f"{API}/records/999", json={"row_version": 1})

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_bulk_update_records_returns_changed_count(client, make_dataset):
    # 준비
    made = await make_dataset("일괄", [Row("하나", "가"), Row("둘", "가")])
    ids = [record.id for record in made.records]

    # 실행
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/records/bulk",
        json={"record_ids": ids, "action": "exclude"},
    )

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"changed": 2}


async def test_empty_trash_deletes_only_trashed_records_of_the_dataset(
    client, db_session, make_dataset
):
    # 준비
    made = await make_dataset(
        "비울 것",
        [
            Row("남을 문장", "가"),
            Row("버린 문장", "가", trashed=True),
            Row("또 버림", "나", trashed=True),
        ],
    )
    other = await make_dataset("다른 것", [Row("다른 데이터셋의 버린 문장", "가", trashed=True)])

    # 실행
    response = await client.delete(f"{API}/datasets/{made.dataset.id}/trash")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"changed": 2}
    remaining = await db_session.scalars(
        select(Record.text).where(Record.dataset_id == made.dataset.id)
    )
    assert remaining.all() == ["남을 문장"]
    assert await _count(db_session, Record, dataset_id=other.dataset.id) == 1
    dataset = (await client.get(f"{API}/datasets/{made.dataset.id}")).json()
    assert (dataset["record_count"], dataset["trash_count"]) == (1, 0)


async def test_empty_trash_removes_map_points_of_trashed_records(client, db_session, make_dataset):
    # 준비: 지도를 만든 뒤 한 문장을 휴지통에 넣는다.
    made = await make_dataset("지도 뒤 비우기", [Row("안녕", "인사"), Row("환불요", "환불")])
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")
    await run_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())
    trashed_id = made.records[0].id
    await client.post(
        f"{API}/datasets/{made.dataset.id}/records/bulk",
        json={"record_ids": [trashed_id], "action": "trash"},
    )

    # 실행
    response = await client.delete(f"{API}/datasets/{made.dataset.id}/trash")

    # 확인
    assert response.json() == {"changed": 1}
    point_ids = await db_session.scalars(select(MapPoint.record_id))
    assert point_ids.all() == [made.records[1].id]


async def test_empty_trash_returns_404_when_dataset_is_missing(client):
    # 실행
    response = await client.delete(f"{API}/datasets/999/trash")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "데이터셋을 찾을 수 없습니다."}


async def test_bulk_update_records_returns_422_when_no_ids_are_given(client, make_dataset):
    # 준비
    made = await make_dataset("일괄", [])

    # 실행
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/records/bulk",
        json={"record_ids": [], "action": "trash"},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_cleanup_duplicates_and_undo_return_changed_counts(client, make_dataset):
    # 준비
    made = await make_dataset("중복", [Row("같은 말", "가"), Row("같은 말", "가")])
    url = f"{API}/datasets/{made.dataset.id}/cleanup/duplicates"

    # 실행
    cleaned = await client.post(url)
    undone = await client.post(f"{url}/undo")

    # 확인
    assert cleaned.json() == {"changed": 1}
    assert undone.json() == {"changed": 1}


# ---------- 진단 ----------


async def test_get_overview_returns_grades_and_problems(client, make_dataset):
    # 준비
    made = await make_dataset("진단", [Row("같은 문장입니다", "가"), Row("같은 문장입니다", "가")])

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/overview")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert (body["total"], body["included"], body["effective_count"]) == (2, 2, 1)
    assert body["duplicates"]["grade"] == "bad"
    assert body["problems"] == [
        {"kind": "duplicate", "severity": "bad", "count": 1, "label_id": None}
    ]
    assert body["semantic"]["available"] is False
    assert body["computed_at"]


async def test_get_overview_returns_thresholds_and_examples(client, make_dataset):
    # 준비
    made = await make_dataset(
        "진단",
        [Row("같은 문장입니다", "가"), Row("같은 문장입니다", "가"), Row("ok", "나")],
    )

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/overview")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert set(body["thresholds"]) == {"balance", "duplicates", "conflicts", "short"}
    assert body["thresholds"]["short"] == {
        "unit": "records",
        "steps": [
            {"grade": "good", "op": "le", "value": 0},
            {"grade": "warn", "op": None, "value": None},
        ],
    }
    assert body["examples"]["duplicate"] == {
        "text": "같은 문장입니다",
        "label_name": "가",
        "copies": 2,
    }
    assert body["examples"]["conflict"] is None
    assert body["examples"]["short"] == [
        {"record_id": made.records[2].id, "text": "ok", "length": 2}
    ]


async def test_get_overview_returns_404_when_dataset_is_missing(client):
    # 실행
    response = await client.get(f"{API}/datasets/999/overview")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------- 문장 하나 ----------


async def test_get_record_returns_record_with_label_name(client, make_dataset):
    # 준비
    made = await make_dataset("하나", [Row("안녕하세요", "인사")])

    # 실행
    response = await client.get(f"{API}/records/{made.records[0].id}")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["label_name"] == "인사"


async def test_get_record_returns_404_when_missing(client):
    # 실행
    response = await client.get(f"{API}/records/999999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------- 의미 지도 ----------

MAP_ROWS = [
    Row("배송이 너무 늦어요", "배송"),
    Row("환불은 언제 되나요", "환불"),
    Row("상품이 망가져서 왔어요", "품질"),
    Row("포장 상태가 좋아요", "품질"),
    Row("배송 기사님이 친절해요", "배송"),
]


async def test_start_map_returns_202_with_queued_run_and_job(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)
    await connect_embedding(db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 확인
    body = response.json()
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert body["map"] is None
    assert body["run"]["status"] == "queued"
    assert body["run"]["job_id"] is not None
    job = await db_session.get(Job, body["run"]["job_id"])
    assert job is not None and (job.module, job.kind) == ("classification", "map")


async def test_start_map_returns_422_when_embedding_is_not_connected(client, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"].startswith("임베딩 미연결")


async def test_start_map_returns_409_when_already_building(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT


async def test_get_map_returns_empty_state_without_map(client, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/map")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"map": None, "run": None, "outdated": False, "checks": None}


async def test_get_map_returns_404_when_dataset_is_missing(client):
    # 실행
    response = await client.get(f"{API}/datasets/999999/map")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_map_points_return_columns_after_map_is_built(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")
    await run_map(db_session, dataset_id=made.dataset.id, transport=vector_transport())

    # 실행
    state = await client.get(f"{API}/datasets/{made.dataset.id}/map")
    points = await client.get(f"{API}/datasets/{made.dataset.id}/map/points")

    # 확인
    map_body = state.json()["map"]
    assert map_body["status"] == "done"
    assert map_body["model_name"] == "test-embedding"
    assert map_body["point_count"] == 5
    body = points.json()
    assert points.status_code == status.HTTP_200_OK
    assert body["record_ids"] == [record.id for record in made.records]
    columns = ("x", "y", "label_ids", "flags")
    assert all(len(body[column]) == 5 for column in columns)
    assert "splits" not in body


async def test_map_points_return_404_without_map(client, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/map/points")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_map_matches_return_ids_of_records_with_search_text(client, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)

    # 실행
    response = await client.get(
        f"{API}/datasets/{made.dataset.id}/map/matches", params={"q": "배송"}
    )

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"record_ids": [made.records[0].id, made.records[4].id]}


async def test_cancel_map_stops_queued_run(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)
    await connect_embedding(db_session)
    await client.post(f"{API}/datasets/{made.dataset.id}/map")

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/map/cancel")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["run"]["status"] == "canceled"


async def test_cancel_map_returns_409_when_nothing_is_building(client, make_dataset):
    # 준비
    made = await make_dataset("지도", MAP_ROWS)

    # 실행
    response = await client.post(f"{API}/datasets/{made.dataset.id}/map/cancel")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
