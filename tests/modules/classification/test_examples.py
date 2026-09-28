"""분류 내장 예시 데이터 테스트: 목록 · 넣기(보통 가져오기 길) · 심은 문제가 진단에 정답지의 수만큼 보이는지 · 409 · 404."""

import json

import pytest
from fastapi import status

from dent.modules.classification import examples as examples_service
from dent.modules.classification import overview as overview_service
from dent.modules.classification.jobs import run_import_job
from dent.system import jobs

API = "/api/v1/classification"


def _answers(key: str) -> dict:
    path = examples_service.EXAMPLE_DATA_DIR / f"{key}.answers.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _install(client, db_session, key: str) -> int:
    """예시를 넣고 작업 실행기처럼 가져오기를 끝까지 돌린다. 데이터셋 번호."""
    response = await client.post(f"{API}/examples/{key}")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    created = response.json()
    job = await jobs.get_job(db_session, job_id=created["job_id"])
    await run_import_job(job)
    return int(created["dataset_id"])


async def test_list_examples_shows_planted_problems_and_nothing_installed(client):
    # 실행
    response = await client.get(f"{API}/examples")

    # 확인
    items = {item["key"]: item for item in response.json()}
    assert set(items) == {"topics", "intents"}
    assert items["topics"]["planted"] and items["topics"]["rows"] > 1_000
    assert items["intents"]["planted"] == []
    assert all(item["dataset_id"] is None for item in items.values())


async def test_install_topics_shows_every_planted_text_problem(client, db_session):
    # 준비
    answers = _answers("topics")["checks"]

    # 실행
    dataset_id = await _install(client, db_session, "topics")

    # 확인: 원본 분할 열이 있어도 하나로 합쳐 들어오고, 심은 문제가 정답지의 수만큼 보인다.
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    assert overview.total == _answers("topics")["rows"]
    assert overview.duplicates.groups == answers["duplicates"]["groups"]
    assert overview.duplicates.extra_records == answers["duplicates"]["extra_records"]
    assert overview.conflicts.groups == answers["conflicts"]["groups"]
    assert overview.conflicts.records == answers["conflicts"]["records"]
    assert overview.short.records == answers["short"]["records"]
    assert overview.effective_count == answers["effective_count"]
    assert overview.balance.ratio == pytest.approx(answers["balance_ratio"], abs=0.01)
    assert {label.name: label.included for label in overview.labels} == answers["labels"]
    kinds = {problem.kind for problem in overview.problems}
    assert {"duplicate", "conflict", "short", "imbalance"} <= kinds
    listed = {item["key"]: item for item in (await client.get(f"{API}/examples")).json()}
    assert listed["topics"]["dataset_id"] == dataset_id
    detail = (await client.get(f"{API}/datasets/{dataset_id}")).json()
    assert detail["description"].startswith("백과 문장")


async def test_install_intents_passes_every_text_check(client, db_session):
    # 실행
    dataset_id = await _install(client, db_session, "intents")

    # 확인: 문제가 없는 대조군 (엑셀 · 시트 '문의')
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    assert overview.total == _answers("intents")["rows"]
    assert overview.problems == []
    assert overview.balance.ratio == pytest.approx(1.0)


async def test_install_example_returns_409_when_already_installed(client, db_session):
    # 준비
    await _install(client, db_session, "intents")

    # 실행
    response = await client.post(f"{API}/examples/intents")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "이미 넣은 예시입니다. 다시 넣으려면 그 데이터셋을 먼저 지우세요."
    }


async def test_install_example_returns_404_for_unknown_key(client):
    # 실행
    response = await client.post(f"{API}/examples/nothing")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "예시 데이터를 찾을 수 없습니다."}
