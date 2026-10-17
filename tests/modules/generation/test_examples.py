"""생성 내장 예시 데이터 테스트: 넣어서(보통 가져오기 길) 심은 문제가 정답지의 수만큼 진단에 보이는지 본다."""

import json

import pytest
from fastapi import status

from dent.modules.generation import examples as examples_service
from dent.modules.generation import overview as overview_service
from dent.modules.generation.jobs import run_import_job
from dent.modules.generation.rules import RULE_CHECKS
from dent.system import jobs

API = "/api/v1/generation"


def _answers(key: str) -> dict:
    path = examples_service.EXAMPLE_DATA_DIR / f"{key}.answers.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _install(client, db_session, key: str) -> int:
    """예시를 넣고 가져오기를 끝까지 돌린다. 데이터셋 번호."""
    response = await client.post(f"{API}/examples/{key}")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    created = response.json()
    job = await jobs.get_job(db_session, job_id=created["job_id"])
    await run_import_job(job)
    return int(created["dataset_id"])


async def _rule_counts(db_session, dataset_id: int) -> tuple[int, dict[str, int]]:
    """(대화 수, 규칙의 문제 검사마다 수). 있어야 좋은 검사(안전 예시)는 문제가 아니라 뺀다."""
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    counts = {
        check.key.value: check.count
        for check in overview.checks
        if check.key in RULE_CHECKS and check.kind != "none"
    }
    return overview.conversations, counts


async def test_list_examples_shows_entries_with_planted_problems(client):
    # 실행
    response = await client.get(f"{API}/examples")

    # 확인
    items = response.json()
    assert [item["key"] for item in items] == ["sft", "chat", "prompts", "tools", "pref"]
    assert items[0]["planted"]
    assert items[1]["planted"] == []
    assert all(item["dataset_id"] is None for item in items)


async def test_install_sft_shows_planted_rule_problems(client, db_session):
    # 준비
    answers = _answers("sft")

    # 실행
    dataset_id = await _install(client, db_session, "sft")

    # 확인: 심은 규칙 검사는 정답지의 수만큼, 심지 않은 검사는 0
    conversations, counts = await _rule_counts(db_session, dataset_id)
    assert conversations == answers["rows"]
    expected = {key: answers["checks"].get(key, 0) for key in counts}
    assert counts == expected


@pytest.mark.parametrize("key", ["prompts", "tools", "pref"])
async def test_install_new_shapes_show_planted_rule_problems(client, db_session, key):
    # 준비
    answers = _answers(key)

    # 실행
    dataset_id = await _install(client, db_session, key)

    # 확인: 지시만 · 도구 호출 · 선호 쌍도 정답지의 수만큼 (선호 쌍은 쌍 수가 줄 수)
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    _conversations, counts = await _rule_counts(db_session, dataset_id)
    rows = overview.pairs if key == "pref" else overview.conversations
    assert rows == answers["rows"]
    assert counts == {name: answers["checks"].get(name, 0) for name in counts}


async def test_install_chat_shows_no_problems(client, db_session):
    # 실행
    dataset_id = await _install(client, db_session, "chat")

    # 확인: 대조군은 규칙 검사가 모두 0
    conversations, counts = await _rule_counts(db_session, dataset_id)
    assert conversations == _answers("chat")["rows"]
    assert set(counts.values()) == {0}


async def test_install_example_returns_409_when_installed_and_404_when_unknown(client, db_session):
    # 준비
    await _install(client, db_session, "chat")

    # 실행
    again = await client.post(f"{API}/examples/chat")
    unknown = await client.post(f"{API}/examples/없는예시")
    listed = (await client.get(f"{API}/examples")).json()

    # 확인
    assert again.status_code == status.HTTP_409_CONFLICT
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    assert {item["key"]: item["dataset_id"] is not None for item in listed} == {
        "sft": False,
        "chat": True,
        "prompts": False,
        "tools": False,
        "pref": False,
    }
