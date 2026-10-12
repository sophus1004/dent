"""생성 API 테스트: 주소, 상태 코드, 응답 모양, 오류 문장. 가져오기는 jsonl을 올려 작업 처리 함수로 끝까지 돌린다.

파일 올리기 · 허깅페이스 미리 보기 · 가져오기 기록 · 내려받기는 시스템 API 테스트(tests/api)가 본다.
"""

import json

from fastapi import status

from dent.modules.generation.exporting import BLOCKED_MESSAGE
from dent.modules.generation.jobs import run_export_job
from dent.system import exports as exports_service
from dent.system import jobs
from tests.modules.generation.helpers import (
    CLEAN,
    conversation,
    import_file,
    jsonl_bytes,
    messages_rows,
)

API = "/api/v1/generation"

# 문제가 섞인 대화들: 빈 지시 · 개인정보 · 반복 루프 · 짧은 응답 · 같은 지시 두 번 · 깨끗한 것
MIXED = [
    conversation("", "코드는 다음과 같습니다."),
    conversation("요약해 주세요. 연락처 john@example.com", "채용 안내입니다."),
    conversation("레시피를 써 주세요.", "감자를 으깹니다. " + "으깬 감자로 " * 20),
    conversation("다음 문장에 동사가 있으면 1을 출력하세요.", "0."),
    conversation("서울의 인구는?", "약 940만 명입니다."),
    conversation("서울의 인구는?", "940만 명쯤입니다."),
    CLEAN,
]


async def _mixed_dataset(db_session) -> int:
    finished = await import_file(db_session, jsonl_bytes(messages_rows(MIXED)))
    return finished.dataset_id


def _check(body: dict, key: str) -> dict:
    return next(check for check in body["checks"] if check["key"] == key)


# ---------- 데이터셋 · 필드 맞춤 ----------


async def test_list_datasets_counts_conversations_and_problems(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)

    # 실행
    response = await client.get(f"{API}/datasets")

    # 확인: 문제 대화 수는 같은 지시를 빼고 센다(빈 지시 · 개인정보 · 루프 · 짧은 응답)
    [item] = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert item["id"] == dataset_id
    assert item["conversation_count"] == len(MIXED)
    assert item["problem_count"] == 4
    assert item["importing"] is False


async def test_get_dataset_returns_404_for_other_module_dataset(client):
    # 실행
    response = await client.get(f"{API}/datasets/999")

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_suggest_mapping_picks_turn_columns_first(client):
    # 실행: 번역 데이터셋처럼 영어 원본 대화와 한국어 턴 칸이 함께 있다
    response = await client.post(
        f"{API}/mapping/suggest",
        json={"columns": ["input1", "output1", "input2", "output2", "conversations"]},
    )

    # 확인
    body = response.json()
    assert body["shape"] == "turns"
    assert body["turns"] == [
        {"user": "input1", "assistant": "output1"},
        {"user": "input2", "assistant": "output2"},
    ]


async def test_create_import_returns_422_when_shape_columns_are_missing(client):
    # 실행
    response = await client.post(
        f"{API}/imports",
        json={
            "new_dataset_name": "빠진 칸",
            "source": "file",
            "upload_id": "0" * 32,
            "mapping": {"shape": "alpaca", "output": "output"},
        },
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ---------- 진단 ----------


async def test_overview_counts_checks_and_grades(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/overview")

    # 확인: 빈 지시는 하나라도 심각, 루프 · 짧은 응답은 주의, 같은 지시는 여분 1(7건 중 14%)이라 심각
    body = response.json()
    assert response.status_code == status.HTTP_200_OK
    assert body["conversations"] == len(MIXED)
    assert body["turns"] == {"1": 6, "2": 1}
    assert (_check(body, "empty_prompt")["count"], _check(body, "empty_prompt")["grade"]) == (
        1,
        "bad",
    )
    assert (_check(body, "loop")["count"], _check(body, "loop")["grade"]) == (1, "warn")
    assert (_check(body, "short")["count"], _check(body, "short")["grade"]) == (1, "warn")
    assert (_check(body, "duplicate")["count"], _check(body, "duplicate")["grade"]) == (1, "bad")
    assert _check(body, "short")["who"] == ["rule", "jev"]
    assert body["now_stage"] == 1


# ---------- 대화 · 검토 ----------


async def test_list_conversations_filters_by_problem_with_snippets(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)

    # 실행
    loop = await client.get(
        f"{API}/datasets/{dataset_id}/conversations", params={"problem": "loop"}
    )
    clean = await client.get(
        f"{API}/datasets/{dataset_id}/conversations", params={"problem": "none"}
    )

    # 확인: 문제마다 자리와 조각을 자리 순으로 싣는다(루프는 문장이 끝나지 않아 잘린 응답도 함께 걸린다)
    [item] = loop.json()["items"]
    assert [p["key"] for p in item["problems"]] == ["loop", "truncated"]
    problem = item["problems"][0]
    assert problem["turn"] == 1
    assert "으깬 감자로" in problem["snippet"]
    assert item["messages"][-1]["role"] == "assistant"
    assert clean.json()["total"] == 2


async def test_review_queue_lists_duplicate_extras_after_first(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    everything = await client.get(f"{API}/datasets/{dataset_id}/conversations")
    ids = [item["id"] for item in everything.json()["items"]]

    # 실행
    response = await client.get(
        f"{API}/datasets/{dataset_id}/review", params={"check": "duplicate"}
    )

    # 확인: 같은 지시 무리에서 번호가 가장 작은 것은 남기고 뒤의 것만
    assert response.json() == {"check": "duplicate", "ids": [ids[5]], "total": 1}


async def test_decide_keep_removes_problem_and_undo_brings_it_back(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    queue = await client.get(f"{API}/datasets/{dataset_id}/review", params={"check": "short"})
    [conversation_id] = queue.json()["ids"]

    # 실행: '0.'은 분류 과제의 맞는 답이라 그대로 둔다
    decided = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "short", "action": "keep"}
    )
    change_id = decided.json()["change"]["id"]
    after_keep = await client.get(f"{API}/datasets/{dataset_id}/overview")
    undone = await client.post(f"{API}/changes/{change_id}/undo")

    # 확인
    assert decided.status_code == status.HTTP_200_OK
    assert decided.json()["conversation"]["kept"] == ["short"]
    assert decided.json()["conversation"]["problems"] == []
    assert _check(after_keep.json(), "short")["count"] == 0
    assert undone.json()["kept"] == []
    assert [p["key"] for p in undone.json()["problems"]] == ["short"]


async def test_decide_mask_hides_pii_and_rechecks(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    queue = await client.get(f"{API}/datasets/{dataset_id}/review", params={"check": "pii"})
    [conversation_id] = queue.json()["ids"]

    # 실행
    response = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "pii", "action": "mask"}
    )

    # 확인: 글이 바뀌고 규칙 검사를 다시 재 문제가 사라진다
    body = response.json()["conversation"]
    assert body["messages"][0]["content"] == "요약해 주세요. 연락처 [이메일]"
    assert body["problems"] == []


async def test_decide_cut_then_undo_restores_text(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    queue = await client.get(f"{API}/datasets/{dataset_id}/review", params={"check": "loop"})
    [conversation_id] = queue.json()["ids"]

    # 실행
    cut = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "loop", "action": "cut"}
    )
    undone = await client.post(f"{API}/changes/{cut.json()['change']['id']}/undo")
    again = await client.post(f"{API}/changes/{cut.json()['change']['id']}/undo")

    # 확인: 자르면 첫 조각만 남고, 되돌리면 원래 글로, 두 번 되돌리면 409
    assert cut.json()["conversation"]["messages"][-1]["content"] == "감자를 으깹니다. 으깬 감자로"
    assert undone.json()["messages"][-1]["content"].count("으깬 감자로") == 20
    assert again.status_code == status.HTTP_409_CONFLICT
    assert again.json() == {"detail": "이미 되돌린 결정입니다."}


async def test_decide_cut_trims_loop_in_earlier_answer_of_multi_turn(client, db_session):
    # 준비: 루프가 마지막 응답이 아니라 앞 턴의 응답(맥락)에 있다
    looping = [
        {"role": "user", "content": "레시피를 써 주세요."},
        {"role": "assistant", "content": "감자를 으깹니다. " + "으깬 감자로 " * 20},
        {"role": "user", "content": "고마워요."},
        {"role": "assistant", "content": "맛있게 드세요."},
    ]
    finished = await import_file(db_session, jsonl_bytes(messages_rows([looping])))
    queue = await client.get(
        f"{API}/datasets/{finished.dataset_id}/review", params={"check": "loop"}
    )
    [conversation_id] = queue.json()["ids"]

    # 실행
    cut = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "loop", "action": "cut"}
    )

    # 확인: 앞 턴의 응답이 잘리고 마지막 응답은 그대로, 루프 문제가 풀린다
    body = cut.json()["conversation"]
    assert cut.status_code == status.HTTP_200_OK
    assert body["messages"][1]["content"] == "감자를 으깹니다. 으깬 감자로"
    assert body["messages"][-1]["content"] == "맛있게 드세요."
    assert "loop" not in [problem["key"] for problem in body["problems"]]


async def test_decide_returns_422_for_action_the_check_does_not_allow(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    queue = await client.get(f"{API}/datasets/{dataset_id}/review", params={"check": "short"})
    [conversation_id] = queue.json()["ids"]

    # 실행: 짧은 응답에는 자르기가 없다
    response = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "short", "action": "cut"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "이 검사에서는 할 수 없는 결정입니다."}


async def test_decide_returns_409_when_problem_is_already_gone(client, db_session):
    # 준비: 깨끗한 대화
    dataset_id = await _mixed_dataset(db_session)
    clean = await client.get(
        f"{API}/datasets/{dataset_id}/conversations", params={"problem": "none"}
    )
    conversation_id = clean.json()["items"][0]["id"]

    # 실행
    response = await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "loop", "action": "drop"}
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT


async def test_decide_all_drops_remaining_and_logs_changes(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)

    # 실행
    response = await client.post(
        f"{API}/datasets/{dataset_id}/decide-all", json={"check": "empty_prompt", "action": "drop"}
    )
    overview = await client.get(f"{API}/datasets/{dataset_id}/overview")
    changes = await client.get(f"{API}/datasets/{dataset_id}/changes")

    # 확인
    assert response.json() == {"changed": 1}
    assert overview.json()["trashed"] == 1
    assert _check(overview.json(), "empty_prompt")["count"] == 0
    assert [(c["check_key"], c["action"]) for c in changes.json()] == [("empty_prompt", "drop")]


# ---------- 휴지통 ----------


async def test_bulk_trash_restore_and_empty_trash(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)
    everything = await client.get(f"{API}/datasets/{dataset_id}/conversations")
    first, second = [item["id"] for item in everything.json()["items"][:2]]

    # 실행
    trashed = await client.post(
        f"{API}/datasets/{dataset_id}/conversations/bulk",
        json={"ids": [first, second], "action": "trash"},
    )
    restored = await client.post(
        f"{API}/datasets/{dataset_id}/conversations/bulk",
        json={"ids": [first], "action": "restore"},
    )
    emptied = await client.delete(f"{API}/datasets/{dataset_id}/trash")
    left = await client.get(
        f"{API}/datasets/{dataset_id}/conversations", params={"status": "trash"}
    )

    # 확인
    assert (trashed.json(), restored.json(), emptied.json()) == (
        {"changed": 2},
        {"changed": 1},
        {"changed": 1},
    )
    assert left.json()["total"] == 0


# ---------- 내보내기 ----------


async def test_create_export_returns_409_while_serious_problems_remain(client, db_session):
    # 준비
    dataset_id = await _mixed_dataset(db_session)

    # 실행
    preview = await client.post(
        f"{API}/datasets/{dataset_id}/exports/preview", json={"formats": ["sft_jsonl"]}
    )
    response = await client.post(
        f"{API}/datasets/{dataset_id}/exports", json={"formats": ["sft_jsonl"]}
    )

    # 확인: 미리 보기는 막힘과 남은 심각을 보이고, 만들기는 409
    assert preview.json()["blocked"] is True
    assert preview.json()["checks"][0]["grade"] == "bad"
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": BLOCKED_MESSAGE}


async def test_export_writes_messages_jsonl_with_source(client, db_session):
    # 준비: 문제 없는 대화만
    finished = await import_file(db_session, jsonl_bytes(messages_rows([CLEAN])))

    # 실행
    response = await client.post(
        f"{API}/datasets/{finished.dataset_id}/exports", json={"formats": ["sft_jsonl"]}
    )
    [export] = response.json()
    job = await jobs.get_job(db_session, job_id=export["job_id"])
    await run_export_job(job)
    made = await exports_service.get_export(db_session, export_id=export["id"])

    # 확인: TRL 대화 모양 + 출처 칸
    assert response.status_code == status.HTTP_201_CREATED
    [line] = exports_service.file_path(made).read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {"messages": CLEAN, "source": "original"}
    assert made.result == {"conversations": 1}
