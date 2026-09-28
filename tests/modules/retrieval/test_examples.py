"""검색 내장 예시 데이터 테스트: 넷을 넣어(보통 가져오기 길) 입구 단계와 심은 문제가 정답지의 수만큼 진단에 보이는지 본다."""

import json

from fastapi import status

from dent.modules.retrieval import examples as examples_service
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval.jobs import run_import_job
from dent.system import imports as imports_service
from dent.system import jobs

API = "/api/v1/retrieval"


def _answers(key: str) -> dict:
    path = examples_service.EXAMPLE_DATA_DIR / f"{key}.answers.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _install(client, db_session, key: str) -> tuple[int, int]:
    """예시를 넣고 가져오기를 끝까지 돌린다. (데이터셋 번호, 가져오기 번호)."""
    response = await client.post(f"{API}/examples/{key}")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    created = response.json()
    job = await jobs.get_job(db_session, job_id=created["job_id"])
    await run_import_job(job)
    return int(created["dataset_id"]), int(created["id"])


async def _checks(db_session, dataset_id: int) -> tuple[dict, dict]:
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)
    return overview.model_dump(), {check.key: check for check in overview.checks}


async def test_list_examples_shows_four_entries(client):
    # 실행
    response = await client.get(f"{API}/examples")

    # 확인
    assert [item["key"] for item in response.json()] == [
        "encyclopedia",
        "reading",
        "triplets",
        "company",
    ]


async def test_install_encyclopedia_shows_document_problems(client, db_session):
    # 준비
    answers = _answers("encyclopedia")["checks"]

    # 실행
    dataset_id, import_id = await _install(client, db_session, "encyclopedia")

    # 확인: 문서만(1단계 입구) · 긴 문서 · 깨진 글자 · 목차 · 표 · 반복 구간 · 똑같은 줄은 가져올 때 합침
    overview, checks = await _checks(db_session, dataset_id)
    assert overview["flow"]["entry"] == 1
    assert overview["document_count"] == answers["documents"]
    finished = await imports_service.get_import(db_session, import_id=import_id)
    assert finished.result["documents_merged"] == answers["merged"]
    assert checks["long"].view_count == answers["long"]
    assert checks["broken"].view_count == answers["broken"]
    assert checks["pick"].view_count == answers["pick"]
    assert checks["repeat"].grade == "warn"


async def test_install_reading_shows_query_problems(client, db_session):
    # 준비
    answers = _answers("reading")["checks"]

    # 실행
    dataset_id, _import_id = await _install(client, db_session, "reading")

    # 확인: 질문 · 지문(2단계 입구) · 원본 분할이 달라도 같은 질문은 하나로
    overview, checks = await _checks(db_session, dataset_id)
    assert overview["flow"]["entry"] == 2
    assert overview["query_count"] == answers["queries"]
    assert checks["context"].view_count == answers["context"]
    assert checks["short_long"].view_count == answers["short_long"]
    assert checks["easy_pair"].view_count == answers["easy_pair"]
    assert checks["long"].view_count == answers["long"]


async def test_install_triplets_shows_negative_problems(client, db_session):
    # 준비
    answers = _answers("triplets")["checks"]

    # 실행
    dataset_id, import_id = await _install(client, db_session, "triplets")

    # 확인: 세 쌍(3단계 입구) · 정답 빈 줄은 건너뜀 · 판정 충돌 · 오답 부족
    overview, checks = await _checks(db_session, dataset_id)
    assert overview["flow"]["entry"] == 3
    # 문서 검사를 모두 통과했으니, 뜻 분석 전이어도 '지금'은 판정 충돌이 남은 질의 단계다.
    assert overview["flow"]["current"] == 2
    assert overview["query_count"] == answers["queries"]
    finished = await imports_service.get_import(db_session, import_id=import_id)
    assert finished.rows_skipped == answers["skipped"]
    assert checks["conflict"].view_count == answers["conflict"]
    assert checks["no_negative"].view_count == answers["no_negative"]


async def test_install_company_shows_long_rules_and_repeated_signature(client, db_session):
    # 준비
    answers = _answers("company")["checks"]

    # 실행
    dataset_id, _import_id = await _install(client, db_session, "company")

    # 확인
    overview, checks = await _checks(db_session, dataset_id)
    assert overview["document_count"] == answers["documents"]
    assert checks["long"].view_count == answers["long"]
    assert checks["repeat"].grade == "warn"


async def test_encyclopedia_pairs_become_duplicates_after_removing_web_address(client, db_session):
    # 준비: 웹 주소 줄만 다른 문서 쌍을 심었다(가져올 때는 따로 들어온다).
    answers = _answers("encyclopedia")["checks"]
    dataset_id, _import_id = await _install(client, db_session, "encyclopedia")
    _overview, before = await _checks(db_session, dataset_id)
    repeats = (await client.get(f"{API}/datasets/{dataset_id}/repeats")).json()["items"]
    url = next(item for item in repeats if item["kind"] == "meta" and item["text"] == "웹 주소")

    # 실행: 웹 주소(메타 모양) 반복 구간을 뗀다.
    response = await client.patch(f"{API}/repeats/{url['id']}", json={"decision": "remove"})
    _overview, after = await _checks(db_session, dataset_id)

    # 확인: 학습 글이 같아진 쌍이 '중복 문서'로 보인다.
    assert response.status_code == status.HTTP_200_OK
    assert url["document_count"] == answers["url_documents"]
    assert before["duplicate_document"].view_count == 0
    assert after["duplicate_document"].view_count == answers["duplicate_pairs"]
