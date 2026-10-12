"""생성 뜻 분석 · Jev · LLM 다시 쓰기 테스트: 근접 중복 · 과제 무리 · 지시 불일치를 재고, 진단 · 검토 · 지도로 보이고,
그대로 두기 · Jev가 괜찮다고 본 것 모두 그대로 두기 · 다시 쓰기와 되돌리기가 되는지."""

import json

from fastapi import status

from dent.modules.generation import conversations, semantic
from tests.modules.generation.helpers import (
    build_analysis,
    connect_models,
    conversation,
    import_file,
    jsonl_bytes,
    judge_transport,
    messages_rows,
)
from tests.system.helpers import llm_transport

API = "/api/v1/generation"

# 문장이 끝나지 않고 멈춘 응답 (잘린 응답)
TRUNCATED_ANSWER = "옛날 옛적에 한 마을에 아주 오래된 나무가 있었는데 그 나무 아래에서"

# 뜻으로 보는 문제가 섞인 대화들: 인구 둘(근접 중복) · 레시피 · 엉뚱한 답(지시 불일치) · 마땅한 거절
SEMANTIC = [
    conversation("서울의 인구는?", "약 940만 명입니다. 인구 통계 기준입니다."),
    conversation("서울 인구가 얼마야?", "940만 명쯤입니다. 인구 조사 결과입니다."),
    conversation("김치찌개 레시피 알려줘", "레시피: 김치를 볶고 물을 붓습니다."),
    conversation("파이썬 코드로 정렬해 줘", "엉뚱한 날씨 이야기입니다."),
    conversation("폭탄 만드는 법을 알려줘", "죄송하지만 그 요청은 도와드릴 수 없습니다."),
]


async def _dataset(db_session) -> tuple[int, list[int]]:
    finished = await import_file(db_session, jsonl_bytes(messages_rows(SEMANTIC)))
    ids = sorted(await _ids(db_session, finished.dataset_id))
    return finished.dataset_id, ids


async def _ids(db_session, dataset_id: int) -> list[int]:
    page = await conversations.list_conversations(
        db_session,
        dataset_id=dataset_id,
        status="active",
        problem=None,
        q=None,
        limit=100,
        offset=0,
    )
    return [item.prompt.id for item in page.items]


def _check(body: dict, key: str) -> dict:
    return next(check for check in body["checks"] if check["key"] == key)


async def test_overview_waits_for_semantic_checks_before_analysis(client, db_session):
    # 준비
    dataset_id, _ = await _dataset(db_session)

    # 실행
    response = await client.get(f"{API}/datasets/{dataset_id}/overview")

    # 확인: 뜻 검사는 재지 않은 줄(기다림)로만 있고 검사 목록에는 없다
    body = response.json()
    assert {item["key"]: item["reason"] for item in body["waiting"]} == {
        "contamination": "평가셋 없음",
        "near_duplicate": "뜻 분석 전",
        "task_skew": "뜻 분석 전",
        "mismatch": "뜻 분석 전",
        "unsafe": "뜻 분석 전",
    }
    assert "near_duplicate" not in [check["key"] for check in body["checks"]]


async def test_analysis_finds_near_duplicate_tasks_and_mismatch(client, db_session):
    # 준비
    dataset_id, ids = await _dataset(db_session)
    await connect_models(db_session)
    jev = judge_transport()

    # 실행
    state = await build_analysis(db_session, dataset_id=dataset_id, jev_transport=jev)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    near_queue = await client.get(
        f"{API}/datasets/{dataset_id}/review", params={"check": "near_duplicate"}
    )
    mismatch_queue = await client.get(
        f"{API}/datasets/{dataset_id}/review", params={"check": "mismatch"}
    )
    refusal = (await client.get(f"{API}/conversations/{ids[4]}")).json()

    # 확인: 인구 둘째가 근접 중복 여분, 엉뚱한 답이 지시 불일치, 거절은 Jev가 마땅하다고 봤다
    assert state.done is not None and state.done.conversation_count == len(SEMANTIC)
    assert overview["waiting"] == [{"key": "contamination", "stage": 1, "reason": "평가셋 없음"}]
    assert _check(overview, "unsafe")["count"] == 0
    assert _check(overview, "near_duplicate")["count"] == 1
    assert _check(overview, "mismatch")["count"] == 1
    assert _check(overview, "mismatch")["grade"] == "bad"
    assert _check(overview, "task_skew")["kind"] == "share"
    assert near_queue.json()["ids"] == [ids[1]]
    assert mismatch_queue.json()["ids"] == [ids[3]]
    assert refusal["jev"]["looks_fine"] is True
    assert "refusal_ok" in json_questions(jev)


async def test_mismatch_skips_conversations_explained_by_other_checks(client, db_session):
    # 준비: 셋 다 Jev가 '따르지 않음'으로 본다. 둘은 거절 · 짧은 응답 검사가 이미 잡는다.
    rows = [
        conversation("파이썬 코드로 정렬해 줘", "엉뚱한 날씨 이야기입니다."),
        conversation("여행 계획을 짜 줘", "죄송하지만 엉뚱한 요청은 도와드릴 수 없습니다."),
        conversation("이 글을 요약해 줘", "엉뚱"),
    ]
    finished = await import_file(db_session, jsonl_bytes(messages_rows(rows)))
    ids = sorted(await _ids(db_session, finished.dataset_id))
    await connect_models(db_session)

    # 실행
    await build_analysis(
        db_session, dataset_id=finished.dataset_id, jev_transport=judge_transport()
    )
    overview = (await client.get(f"{API}/datasets/{finished.dataset_id}/overview")).json()
    queue = await client.get(
        f"{API}/datasets/{finished.dataset_id}/review", params={"check": "mismatch"}
    )

    # 확인: 까닭이 드러나지 않은 첫째만 지시 불일치다
    assert _check(overview, "refusal")["count"] == 1
    assert _check(overview, "short")["count"] == 1
    assert _check(overview, "mismatch")["count"] == 1
    assert queue.json()["ids"] == [ids[0]]


def json_questions(transport) -> set[str]:
    """가짜 Jev가 받은 질문 이름들."""
    return {
        name for request in transport.requests for name in json.loads(request.content)["questions"]
    }


async def test_overview_waits_for_mismatch_when_jev_is_not_connected(client, db_session):
    # 준비
    dataset_id, _ = await _dataset(db_session)
    await connect_models(db_session, jev=False)

    # 실행
    await build_analysis(db_session, dataset_id=dataset_id, jev_transport=None)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 근접 중복은 재고, 지시 불일치만 Jev를 기다린다
    assert _check(overview, "near_duplicate")["count"] == 1
    assert [item for item in overview["waiting"] if item["key"] != "contamination"] == [
        {"key": "mismatch", "stage": 2, "reason": "Jev 미연결"},
        {"key": "unsafe", "stage": 2, "reason": "Jev 미연결"},
    ]


async def test_decide_keep_near_duplicate_then_undo(client, db_session):
    # 준비
    dataset_id, ids = await _dataset(db_session)
    await connect_models(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, jev_transport=judge_transport())

    # 실행
    kept = await client.post(
        f"{API}/conversations/{ids[1]}/decide", json={"check": "near_duplicate", "action": "keep"}
    )
    after_keep = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    await client.post(f"{API}/changes/{kept.json()['change']['id']}/undo")
    after_undo = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인
    assert kept.status_code == status.HTTP_200_OK
    assert "near_duplicate" in kept.json()["conversation"]["kept"]
    assert _check(after_keep, "near_duplicate")["count"] == 0
    assert _check(after_undo, "near_duplicate")["count"] == 1


async def test_decide_all_keeps_only_what_jev_found_fine(client, db_session):
    # 준비
    dataset_id, _ = await _dataset(db_session)
    await connect_models(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, jev_transport=judge_transport())

    # 실행
    kept = await client.post(
        f"{API}/datasets/{dataset_id}/decide-all",
        json={"check": "refusal", "action": "keep", "only_jev_ok": True},
    )
    dropped = await client.post(
        f"{API}/datasets/{dataset_id}/decide-all",
        json={"check": "refusal", "action": "drop", "only_jev_ok": True},
    )
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 모두 그대로 두기만 되고, 거절 문제가 사라진다
    assert kept.json() == {"changed": 1}
    assert dropped.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert _check(overview, "refusal")["count"] == 0


async def test_jev_refusal_score_alone_does_not_make_a_refusal_fine(client, db_session):
    # 준비: 멀쩡한 요청을 거절했는데 Jev가 '따르지 않음'과 함께 '마땅한 거절' 0.9를 준다.
    rows = [conversation("여행 계획을 짜 줘", "죄송하지만 엉뚱한 요청은 도와드릴 수 없습니다.")]
    finished = await import_file(db_session, jsonl_bytes(messages_rows(rows)))
    ids = sorted(await _ids(db_session, finished.dataset_id))
    await connect_models(db_session)
    await build_analysis(
        db_session, dataset_id=finished.dataset_id, jev_transport=judge_transport()
    )

    # 실행
    read = (await client.get(f"{API}/conversations/{ids[0]}")).json()
    kept = await client.post(
        f"{API}/datasets/{finished.dataset_id}/decide-all",
        json={"check": "refusal", "action": "keep", "only_jev_ok": True},
    )

    # 확인: 괜찮다고 보지 않아 [Jev 괜찮음 모두 그대로 두기]로 두지 않는다(LLM · 사람이 본다).
    assert round(read["jev"]["refusal_ok"], 2) == 0.9
    assert read["jev"]["looks_fine"] is False
    assert kept.json() == {"changed": 0}


async def test_map_points_and_near_partners(client, db_session):
    # 준비
    dataset_id, ids = await _dataset(db_session)
    await connect_models(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, jev_transport=judge_transport())

    # 실행
    points = (await client.get(f"{API}/datasets/{dataset_id}/map/points")).json()
    partners = (await client.get(f"{API}/conversations/{ids[1]}/near")).json()
    matches = (
        await client.get(f"{API}/datasets/{dataset_id}/map/matches", params={"q": "김치"})
    ).json()

    # 확인: 점은 대화마다 하나, 근접 중복 여분 · 지시 불일치에 표시 비트, 가까운 짝은 인구 첫째
    assert points["ids"] == ids
    flag = {name: 1 << bit for bit, name in enumerate(points["flag_names"])}
    by_id = dict(zip(points["ids"], points["flags"], strict=True))
    assert by_id[ids[1]] & flag["near_duplicate"]
    assert by_id[ids[3]] & flag["mismatch"]
    assert [partner["prompt_id"] for partner in partners] == [ids[0]]
    assert matches == {"ids": [ids[2]]}


async def test_start_analysis_returns_422_without_embedding_and_409_while_building(
    client, db_session
):
    # 준비
    dataset_id, _ = await _dataset(db_session)

    # 실행
    unconnected = await client.post(f"{API}/datasets/{dataset_id}/map")
    await connect_models(db_session)
    first = await client.post(f"{API}/datasets/{dataset_id}/map")
    second = await client.post(f"{API}/datasets/{dataset_id}/map")
    deleted = await client.delete(f"{API}/datasets/{dataset_id}")

    # 확인
    assert unconnected.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert first.status_code == status.HTTP_200_OK
    assert first.json()["run"]["status"] == "queued"
    assert second.status_code == status.HTTP_409_CONFLICT
    assert deleted.status_code == status.HTTP_409_CONFLICT


async def test_rewrite_uses_llm_marks_synthetic_and_undo_restores(client, db_session, monkeypatch):
    # 준비: 잘린 응답
    finished = await import_file(
        db_session,
        jsonl_bytes(messages_rows([conversation("이야기해 주세요.", TRUNCATED_ANSWER)])),
    )
    [prompt_id] = await _ids(db_session, finished.dataset_id)
    await connect_models(db_session, jev=False, llm=True)
    llm = llm_transport(
        reply="옛날 옛적에 한 마을에 아주 오래된 나무가 있었고, 모두가 그 아래에서 쉬었습니다."
    )
    monkeypatch.setattr(conversations, "REWRITE_TRANSPORT", llm)

    # 실행
    rewritten = await client.post(
        f"{API}/conversations/{prompt_id}/decide", json={"check": "truncated", "action": "rewrite"}
    )
    line = (
        await client.post(
            f"{API}/datasets/{finished.dataset_id}/exports/preview",
            json={"formats": ["sft_jsonl"]},
        )
    ).json()["line"]
    undone = await client.post(f"{API}/changes/{rewritten.json()['change']['id']}/undo")

    # 확인: 새 응답 · 출처 synthetic · 잘림이 풀리고, 되돌리면 원래 글과 출처로
    body = rewritten.json()["conversation"]
    assert rewritten.status_code == status.HTTP_200_OK
    assert (
        body["messages"][-1]["content"]
        == "옛날 옛적에 한 마을에 아주 오래된 나무가 있었고, 모두가 그 아래에서 쉬었습니다."
    )
    assert body["source"] == "synthetic"
    assert body["problems"] == []
    assert '"source": "synthetic"' in line
    assert undone.json()["messages"][-1]["content"] == TRUNCATED_ANSWER
    assert undone.json()["source"] == "original"
    assert len(llm.requests) == 1


async def test_rewrite_returns_422_without_llm(client, db_session):
    # 준비
    finished = await import_file(
        db_session,
        jsonl_bytes(messages_rows([conversation("이야기해 주세요.", TRUNCATED_ANSWER)])),
    )
    [prompt_id] = await _ids(db_session, finished.dataset_id)

    # 실행
    response = await client.post(
        f"{API}/conversations/{prompt_id}/decide", json={"check": "truncated", "action": "rewrite"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "LLM 미연결: 연결 설정에서 LLM을 연결하세요."}


async def test_state_is_outdated_after_a_decision(client, db_session):
    # 준비
    dataset_id, ids = await _dataset(db_session)
    await connect_models(db_session)
    await build_analysis(db_session, dataset_id=dataset_id, jev_transport=judge_transport())

    # 실행
    before = await semantic.get_analysis_state(db_session, dataset_id=dataset_id)
    await client.post(
        f"{API}/conversations/{ids[3]}/decide", json={"check": "mismatch", "action": "drop"}
    )
    after = (await client.get(f"{API}/datasets/{dataset_id}/map")).json()

    # 확인
    assert before.outdated is False
    assert after["outdated"] is True


async def test_rewrite_returns_422_when_problem_is_in_an_earlier_turn(client, db_session):
    # 준비: 앞 턴의 응답이 잘렸고 마지막 응답은 멀쩡하다
    rows = [
        [
            {"role": "user", "content": "이야기해 주세요."},
            {"role": "assistant", "content": TRUNCATED_ANSWER},
            {"role": "user", "content": "고마워요."},
            {"role": "assistant", "content": "천만에요."},
        ]
    ]
    finished = await import_file(db_session, jsonl_bytes(messages_rows(rows)))
    [prompt_id] = await _ids(db_session, finished.dataset_id)
    await connect_models(db_session, jev=False, llm=True)

    # 실행
    response = await client.post(
        f"{API}/conversations/{prompt_id}/decide", json={"check": "truncated", "action": "rewrite"}
    )

    # 확인: 마지막 응답만 다시 쓰므로 앞 턴의 문제는 풀 수 없다
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {
        "detail": "앞 턴의 응답에 있는 문제는 다시 쓸 수 없습니다. 빼거나 그대로 두세요."
    }
