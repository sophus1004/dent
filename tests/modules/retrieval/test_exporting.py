"""내보내기 테스트: 미리 보기 · 학습 jsonl(오답 풀 전체) · 학습 표 · 평가 BEIR(중복 묶음 정답) · 코퍼스 · 심각이 남으면 409 ·
교사 점수(Jev, 로짓) · 출처 칸. 분할이 없어 모든 파일에 정답이 있는 질의를 모두 넣는다."""

import csv
import io
import json
import math
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status

from dent.modules.retrieval.jobs import run_export_job
from dent.system import exports as exports_service
from dent.system import jobs
from dent.system.models import Job, JobStatus
from tests.modules.retrieval.helpers import connect_jev, contains_jev, import_csv

API = "/api/v1/retrieval"


async def _run_exports(client, db_session, body: dict, *, jev=None) -> list[dict]:
    response = await client.post(f"{API}/datasets/{body['dataset_id']}/exports", json=body["data"])
    assert response.status_code == status.HTTP_201_CREATED, response.text
    made = response.json()
    for item in made:
        job = await jobs.get_job(db_session, job_id=item["job_id"])
        await run_export_job(job, jev_transport=jev)
    return made


async def test_preview_export_shows_line_and_files(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports/preview",
        json={"formats": ["train_jsonl", "beir"], "negatives": 7},
    )

    # 확인
    body = response.json()
    line = json.loads(body["line"])
    assert (line["query"], line["source"]) == ("환불은 며칠 걸리나요", "original")
    assert [file["format"] for file in body["files"]] == ["train_jsonl", "beir"]
    assert [file["queries"] for file in body["files"]] == [3, 3]
    assert body["source_counts"] == {"original": 7}
    assert body["lacking"] == 3
    assert body["blocked"] == []


async def test_export_writes_train_jsonl_table_and_beir(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    made = await _run_exports(
        client,
        db_session,
        {
            "dataset_id": finished.dataset_id,
            "data": {
                "formats": ["train_jsonl", "train_table", "beir"],
                "negatives": 1,
                "teacher_scores": False,
            },
        },
    )

    # 확인
    files = {}
    for item in made:
        export, path = await exports_service.find_file(db_session, export_id=item["id"])
        files[export.format] = path
    lines = [
        json.loads(line) for line in files["train_jsonl"].read_text(encoding="utf-8").splitlines()
    ]
    # jsonl은 오답 풀 전체를 넣는다(학습기가 매번 고른다). 표는 칸 수(1)만큼. 모두 출처 칸이 있다.
    assert lines[0] == {
        "query": "환불은 며칠 걸리나요",
        "pos": ["환불은 영업일 기준 3일 안에 처리됩니다."],
        "neg": ["배송은 이틀 걸립니다.", "회원 가입은 무료입니다."],
        "source": "original",
    }
    assert len(lines) == 3
    rows = list(csv.reader(io.StringIO(files["train_table"].read_text(encoding="utf-8"))))
    assert rows[0] == ["anchor", "positive", "negative_1", "source"]
    assert [row[-1] for row in rows[1:]] == ["original"] * 3
    with zipfile.ZipFile(files["beir"]) as archive:
        names = set(archive.namelist())
        qrels = archive.read("qrels/test.tsv").decode().splitlines()
        queries = [json.loads(line) for line in archive.read("queries.jsonl").decode().splitlines()]
        corpus = [json.loads(line) for line in archive.read("corpus.jsonl").decode().splitlines()]
    # 분할이 없어 모든 질의를 BEIR의 기본 이름(test) 하나에 둔다.
    assert {"corpus.jsonl", "queries.jsonl", "qrels/test.tsv"} <= names
    assert qrels[0] == "query-id\tcorpus-id\tscore"
    assert len(qrels) == 1 + 3
    assert [query["metadata"] for query in queries] == [{"source": "original"}] * 3
    assert all(item["title"] == "" for item in corpus)


async def test_export_table_leaves_out_source_column_when_asked(
    client, db_session, import_triplets
):
    # 준비
    finished = await import_triplets()

    # 실행
    [made] = await _run_exports(
        client,
        db_session,
        {
            "dataset_id": finished.dataset_id,
            "data": {
                "formats": ["train_table"],
                "negatives": 1,
                "teacher_scores": False,
                "source_column": False,
            },
        },
    )

    # 확인
    _export, path = await exports_service.find_file(db_session, export_id=made["id"])
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert rows[0] == ["anchor", "positive", "negative_1"]


async def test_export_returns_409_when_bad_checks_remain(client, db_session):
    # 준비: 오답 판정만 있는 질의가 있다(정답 없음, 심각).
    finished = await import_csv(
        db_session,
        header=["query", "doc", "score"],
        rows=[["환불 기간", "환불은 3일", "1"], ["배송 기간", "배송은 2일", "0"]],
        mapping={"shape": "scored", "query": "query", "document": "doc", "score": "score"},
    )

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports", json={"formats": ["train_jsonl"]}
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "정답 없음" in response.json()["detail"]


async def test_export_fills_teacher_scores_with_jev(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()
    await connect_jev(db_session)

    # 실행
    [made] = await _run_exports(
        client,
        db_session,
        {
            "dataset_id": finished.dataset_id,
            "data": {"formats": ["train_jsonl"], "negatives": 2, "teacher_scores": True},
        },
        jev=contains_jev(0.9),
    )

    # 확인
    _export, path = await exports_service.find_file(db_session, export_id=made["id"])
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    # FlagEmbedding은 온도 없이 softmax를 씌우므로 확률이 아니라 로짓을 넣는다.
    logit = round(math.log(0.9 / 0.1), 4)
    assert first["pos_scores"] == [logit]
    assert first["neg_scores"] == [logit, logit]


async def test_export_returns_422_for_teacher_scores_without_jev(
    client, db_session, import_triplets
):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports",
        json={"formats": ["train_jsonl"], "teacher_scores": True},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "교사 점수를 넣으려면 Jev를 연결하세요."}


async def test_export_corpus_writes_training_texts_and_rules(client, db_session):
    # 준비: 이메일이 든 문서들 · 이메일 모양을 떼기로 고른다.
    finished = await import_csv(
        db_session,
        header=["title", "text"],
        rows=[
            [f"안내 {index}", f"본문 {index}번은 서로 다른 내용이다. 문의 help@example.com"]
            for index in range(4)
        ],
        mapping={"shape": "documents", "text": "text", "title": "title"},
    )
    repeats = (await client.get(f"{API}/datasets/{finished.dataset_id}/repeats")).json()
    meta = next(item for item in repeats["items"] if item["kind"] == "meta")
    await client.patch(f"{API}/repeats/{meta['id']}", json={"decision": "remove"})

    # 실행: 질의가 없어 심각(질의 0)이 남아도 코퍼스만은 낸다.
    [made] = await _run_exports(
        client, db_session, {"dataset_id": finished.dataset_id, "data": {"formats": ["corpus"]}}
    )

    # 확인
    _export, path = await exports_service.find_file(db_session, export_id=made["id"])
    with zipfile.ZipFile(path) as archive:
        corpus = [json.loads(line) for line in archive.read("corpus.jsonl").decode().splitlines()]
        rules = json.loads(archive.read("rules.json"))
    assert len(corpus) == 4
    assert corpus[0]["text"].startswith("안내 0\n")
    assert "help@example.com" not in corpus[0]["text"]
    assert "help@example.com" in corpus[0]["body"]
    assert [rule["kind"] for rule in rules["removed"]] == ["meta"]


async def test_export_beir_counts_duplicate_group_as_positive(client, db_session):
    # 준비: 질의의 정답과 본문이 같은 다른 문서(제목만 다름)가 있다.
    finished = await import_csv(
        db_session,
        header=["query", "positive"],
        rows=[["환불 기간은", "환불은 영업일 기준 3일 안에 처리됩니다."]],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
    )
    await import_csv(
        db_session,
        header=["title", "text"],
        rows=[["자주 묻는 질문", "환불은 영업일 기준 3일 안에 처리됩니다."]],
        mapping={"shape": "documents", "text": "text", "title": "title"},
        dataset_id=finished.dataset_id,
    )

    # 실행
    [made] = await _run_exports(
        client,
        db_session,
        {"dataset_id": finished.dataset_id, "data": {"formats": ["beir"], "teacher_scores": False}},
    )

    # 확인
    _export, path = await exports_service.find_file(db_session, export_id=made["id"])
    with zipfile.ZipFile(path) as archive:
        qrels = archive.read("qrels/test.tsv").decode().splitlines()[1:]
    assert len(qrels) == 2


async def test_preview_counts_teacher_scores_to_fill(client, db_session, import_triplets):
    # 준비
    finished = await import_triplets()

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports/preview",
        json={"formats": ["train_jsonl"]},
    )

    # 확인: 교사 점수는 기본으로 켜져 있고, Jev가 없으면 주의로 알린다.
    body = response.json()
    # 정답 3 + 오답 4 = 7 판정 (분할 없이 모든 질의)
    assert body["teacher_missing"] == 7
    assert body["warnings"]["teacher_no_jev"] == 7
    # 교사 점수를 잰 적이 없으면 걸릴 시간을 어림하지 않는다.
    assert body["teacher_seconds"] is None


async def test_preview_estimates_teacher_time_from_last_measured_speed(
    client, db_session, import_triplets
):
    # 준비: 앞선 내보내기가 교사 점수 10판정에 5초를 썼다(판정마다 0.5초).
    finished = await import_triplets()
    started = datetime(2026, 9, 27, 7, 0, tzinfo=UTC)
    db_session.add(
        Job(
            module="retrieval",
            kind="export",
            status=JobStatus.DONE,
            finished_at=started + timedelta(seconds=6),
            events=[
                {"at": started.isoformat(), "type": "phase", "phase": "teacher", "total": 10},
                {
                    "at": (started + timedelta(seconds=5)).isoformat(),
                    "type": "phase",
                    "phase": "writing",
                    "total": None,
                },
            ],
        )
    )
    await db_session.commit()

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports/preview",
        json={"formats": ["train_jsonl"]},
    )

    # 확인: 물을 판정 7 × 0.5초
    assert response.json()["teacher_seconds"] == pytest.approx(3.5)


async def test_create_export_puts_teacher_files_after_other_files(
    client, db_session, import_triplets
):
    # 준비
    finished = await import_triplets()
    await connect_jev(db_session)

    # 실행: 학습 jsonl을 먼저 골라도
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports",
        json={"formats": ["train_jsonl", "beir", "corpus"]},
    )

    # 확인: 교사 점수를 채울 학습 파일(Jev에 한 판정씩 묻는다)은 빠른 파일 뒤에 줄을 선다.
    assert response.status_code == status.HTTP_201_CREATED
    made = response.json()
    assert [item["format"] for item in made] == ["beir", "corpus", "train_jsonl"]
    assert [item["job_id"] for item in made] == sorted(item["job_id"] for item in made)
