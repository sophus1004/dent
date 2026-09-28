"""분류 내보내기 테스트: 미리 보기(첫 줄 · 파일마다 수 · 출처별 수 · 남은 검사) · 학습 jsonl · 학습 표 · 라벨 목록이
같은 번호를 쓰는지 · 출처 고르기 · 출처 칸 · 심각이 남아도 막지 않음 · 422 · 404.
분할이 없어 학습에 쓰는 문장(학습 제외 · 휴지통 뺌)을 모두 넣는다."""

import csv
import json

from fastapi import status

from dent.modules.classification import service as classification_service
from dent.modules.classification.jobs import run_export_job
from dent.system import exports as exports_service
from dent.system import jobs
from tests.modules.classification.helpers import Row

API = "/api/v1/classification"

ROWS = [
    Row("환불은 언제 되나요", "환불"),
    Row("배송은 얼마나 걸리나요", "배송"),
    Row("주문을 취소하고 싶어요", "환불"),
    Row("택배가 아직 안 왔어요", "배송"),
    Row("결제를 되돌려 주세요", "환불", exclude_reason="manual"),
    Row("휴지통에 넣은 문장입니다", "배송", trashed=True),
    Row("같은 문장인데 라벨이 달라요", "환불"),
    Row("같은 문장인데 라벨이 달라요", "배송"),
]

# 도우미가 만든 새 문장으로 표시할 줄 (ROWS의 자리)
SYNTHETIC_ROW = 3


async def _make(db_session, make_dataset):
    made = await make_dataset("문의", ROWS)
    made.records[SYNTHETIC_ROW].extra = dict(classification_service.NEW_SENTENCE_EXTRA)
    await db_session.commit()
    return made


async def _run_exports(client, db_session, dataset_id: int, data: dict) -> dict[str, str]:
    """내보내기를 넣고 작업 실행기처럼 돌린다. {파일 모양: 파일 글}."""
    response = await client.post(f"{API}/datasets/{dataset_id}/exports", json=data)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    files = {}
    for item in response.json():
        await run_export_job(await jobs.get_job(db_session, job_id=item["job_id"]))
        export, path = await exports_service.find_file(db_session, export_id=item["id"])
        files[export.format] = path.read_text(encoding="utf-8")
    return files


async def test_preview_export_shows_line_counts_and_left_checks(client, db_session, make_dataset):
    # 준비
    made = await _make(db_session, make_dataset)

    # 실행
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/exports/preview",
        json={"formats": ["train_jsonl", "labels"]},
    )

    # 확인: 학습 제외 · 휴지통은 빠지고, 라벨 충돌(심각)은 막지 않고 보이기만 한다.
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert json.loads(body["line"]) == {
        "text": "환불은 언제 되나요",
        "label": 0,
        "label_text": "환불",
        "source": "original",
    }
    assert [(file["format"], file["records"], file["labels"]) for file in body["files"]] == [
        ("train_jsonl", 6, 2),
        ("labels", 0, 2),
    ]
    assert body["files"][0]["file_name"] == "문의-train.jsonl"
    assert body["source_counts"] == {"original": 5, "synthetic": 1}
    assert (body["excluded"], body["unlabeled"]) == (1, 0)
    conflict = next(check for check in body["checks"] if check["key"] == "conflict")
    assert (conflict["name"], conflict["grade"]) == ("라벨 충돌", "bad")
    assert body["checks"][0]["grade"] == "bad"


async def test_export_writes_jsonl_table_and_labels_with_same_numbers(
    client, db_session, make_dataset
):
    # 준비: 라벨 충돌(심각)이 남아 있다.
    made = await _make(db_session, make_dataset)

    # 실행
    files = await _run_exports(
        client,
        db_session,
        made.dataset.id,
        {"formats": ["train_jsonl", "train_table", "labels"]},
    )

    # 확인: 심각이 남아도 만든다. 세 파일이 같은 라벨 번호를 쓰고, 새 문장은 출처가 synthetic이다.
    lines = [json.loads(line) for line in files["train_jsonl"].splitlines()]
    assert [(line["text"], line["label"], line["source"]) for line in lines] == [
        ("환불은 언제 되나요", 0, "original"),
        ("배송은 얼마나 걸리나요", 1, "original"),
        ("주문을 취소하고 싶어요", 0, "original"),
        ("택배가 아직 안 왔어요", 1, "synthetic"),
        ("같은 문장인데 라벨이 달라요", 0, "original"),
        ("같은 문장인데 라벨이 달라요", 1, "original"),
    ]
    table = list(csv.reader(files["train_table"].splitlines()))
    assert table[0] == ["text", "label", "label_text", "source"]
    assert table[4] == ["택배가 아직 안 왔어요", "1", "배송", "synthetic"]
    assert len(table) == len(lines) + 1
    assert json.loads(files["labels"]) == {
        "id2label": {"0": "환불", "1": "배송"},
        "label2id": {"환불": 0, "배송": 1},
    }


async def test_export_keeps_chosen_sources_without_source_column(client, db_session, make_dataset):
    # 준비
    made = await _make(db_session, make_dataset)

    # 실행: 원본만 · 표에 출처 칸 없이
    files = await _run_exports(
        client,
        db_session,
        made.dataset.id,
        {"formats": ["train_table"], "sources": ["original"], "source_column": False},
    )

    # 확인
    table = list(csv.reader(files["train_table"].splitlines()))
    assert table[0] == ["text", "label", "label_text"]
    assert [row[0] for row in table[1:]] == [
        "환불은 언제 되나요",
        "배송은 얼마나 걸리나요",
        "주문을 취소하고 싶어요",
        "같은 문장인데 라벨이 달라요",
        "같은 문장인데 라벨이 달라요",
    ]


async def test_create_export_returns_422_when_no_records_to_export(
    client, db_session, make_dataset
):
    # 준비: 학습에 쓰는 문장이 모두 원본이다.
    made = await make_dataset("원본만", [Row("환불은 언제 되나요", "환불")])

    # 실행: 도우미가 만든 새 문장만 고른다.
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/exports",
        json={"formats": ["train_jsonl"], "sources": ["synthetic"]},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {
        "detail": "내보낼 문장이 없습니다. 학습에 쓰는 문장과 넣을 출처를 확인하세요."
    }


async def test_preview_export_returns_404_when_dataset_is_missing(client):
    # 실행
    response = await client.post(
        f"{API}/datasets/999999/exports/preview", json={"formats": ["train_jsonl"]}
    )

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
