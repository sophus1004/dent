"""분류 service 테스트 (웹 없이): 진단 숫자, 문장 거르기, 고치기, 일괄 작업, 중복 정리, 필드 맞춤 짐작."""

import itertools

import pytest

from dent.modules.classification import mapping as mapping_service
from dent.modules.classification import overview as overview_service
from dent.modules.classification import records as records_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.schemas import (
    ImportCreate,
    LabelCreate,
    LabelUpdate,
    RecordBulkUpdate,
    RecordUpdate,
)
from dent.system import connections as connections_service
from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import ConnectionRole
from dent.system.text import make_text_hash
from tests.modules.classification.helpers import Row

# 진단 숫자를 손으로 셀 수 있게 만든 데이터.
# 인사 6 · 환불 3 · 배송 2 (학습에 쓰는 것), 뺀 것 1, 휴지통 1.
DIAGNOSIS_ROWS = [
    Row("안녕하세요", "인사"),
    Row("안녕하세요", "인사"),
    Row("안녕하세요", "인사"),
    Row("반갑습니다 여러분", "인사"),
    Row("hi", "인사"),
    Row("오늘 날씨 좋네요", "인사"),
    Row("환불해 주세요", "환불"),
    Row("환불 가능한가요", "환불"),
    Row("오늘 날씨 좋네요", "환불"),
    Row("배송 언제 와요", "배송"),
    Row("배송 언제 와요", "배송"),
    Row("뺀 문장입니다", "인사", exclude_reason="manual"),
    Row("휴지통 문장", "환불", trashed=True),
]


async def _list(db_session, dataset_id: int, **filters: object) -> list[str]:
    """거른 문장들의 글자만 모은다."""
    options: dict[str, object] = {
        "status": "active",
        "label_id": None,
        "problem": None,
        "q": None,
        "same_as": None,
        "limit": records_service.MAX_PAGE_SIZE,
        "offset": 0,
    }
    options.update(filters)
    page = await records_service.list_records(db_session, dataset_id=dataset_id, **options)
    return [item.record.text for item in page.items]


# ---------- 진단 ----------


async def test_get_overview_counts_every_health_number(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    assert (overview.total, overview.included, overview.excluded, overview.trashed) == (
        12,
        11,
        1,
        1,
    )
    # 서로 다른 문장은 7개다. 같은 문장은 라벨이 달라도 하나로 센다.
    assert overview.effective_count == 7
    assert overview.balance.ratio == 3.0
    assert (overview.balance.max_label, overview.balance.min_label) == ("인사", "배송")
    assert overview.balance.grade == "bad"
    # 중복은 문장·라벨이 모두 같은 '안녕하세요' 3건 · '배송 언제 와요' 2건 두 무리다(빠질 수 3).
    # '오늘 날씨 좋네요'는 라벨이 달라 충돌로만 센다.
    assert overview.duplicates.groups == 2
    assert overview.duplicates.extra_records == 3
    assert overview.duplicates.rate == pytest.approx(3 / 11)
    assert overview.duplicates.grade == "bad"
    assert (overview.conflicts.groups, overview.conflicts.records) == (1, 2)
    assert overview.conflicts.grade == "bad"
    assert (overview.short.threshold, overview.short.records, overview.short.grade) == (
        5,
        1,
        "warn",
    )
    assert overview.semantic.available is False
    assert "임베딩 서버를 연결하면" in overview.semantic.detail


async def test_get_overview_keeps_semantic_locked_when_embedding_is_connected(
    db_session, make_dataset
):
    # 준비
    made = await make_dataset("뜻 분석", [Row("안녕하세요", "인사")])
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url="http://embedding.test/v1", model="m"
    )

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    assert overview.semantic.available is False
    assert "준비 중" in overview.semantic.detail


async def test_get_overview_describes_each_label(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS, extra_labels=("빈 라벨",))

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    by_name = {label.name: label for label in overview.labels}
    assert [label.name for label in overview.labels] == ["인사", "환불", "배송", "빈 라벨"]
    greeting = by_name["인사"]
    assert (greeting.included, greeting.excluded) == (6, 1)
    assert greeting.share == pytest.approx(6 / 11)
    assert (greeting.min_length, greeting.max_length) == (2, 9)
    assert greeting.duplicate_extra == 2
    assert (greeting.conflict_records, greeting.short_records) == (1, 1)
    refund = by_name["환불"]
    assert (refund.included, refund.excluded, refund.conflict_records) == (3, 0, 1)
    delivery = by_name["배송"]
    assert (delivery.duplicate_extra, delivery.conflict_records) == (1, 0)
    empty = by_name["빈 라벨"]
    assert (empty.included, empty.avg_length, empty.share) == (0, None, 0.0)


async def test_get_overview_lists_problems_bad_first_then_by_count(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    summary = [(problem.kind, problem.severity, problem.count) for problem in overview.problems]
    assert summary == [
        ("duplicate", "bad", 3),
        ("imbalance", "bad", 2),
        ("conflict", "bad", 2),
        ("short", "warn", 1),
    ]
    imbalance = overview.problems[1]
    assert imbalance.label_id == made.labels["배송"].id


async def test_get_overview_builds_length_histogram(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    # 길이가 2~9자뿐이라 1자 폭 막대 8개와 '10자 이상' 막대 하나만 둔다(빈 막대를 늘어놓지 않음).
    bins = overview.length_histogram
    assert len(bins) == 9
    assert sum(item.count for item in bins) == 11
    assert bins[-1].end is None
    counts_by_start = {item.start: item.count for item in bins if item.count}
    assert counts_by_start == {2: 1, 5: 3, 7: 1, 8: 3, 9: 3}


async def test_get_overview_is_all_good_for_clean_balanced_dataset(db_session, make_dataset):
    # 준비
    made = await make_dataset(
        "깨끗한",
        [Row("환불해 주세요", "환불"), Row("배송 언제 와요", "배송")],
    )

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    assert overview.balance.ratio == 1.0
    assert overview.problems == []
    assert overview.effective_count == 2


async def test_get_overview_returns_threshold_ladders_used_for_grades(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    def ladder(threshold):
        return [(step.grade, step.op, step.value) for step in threshold.steps]

    thresholds = overview.thresholds
    assert thresholds.balance.unit == "ratio"
    assert ladder(thresholds.balance) == [
        ("good", "le", 1.5),
        ("warn", "le", 2.5),
        ("bad", None, None),
    ]
    assert thresholds.duplicates.unit == "rate"
    assert ladder(thresholds.duplicates) == [
        ("good", "lt", 0.02),
        ("warn", "lt", 0.10),
        ("bad", None, None),
    ]
    assert thresholds.conflicts.unit == "rate"
    assert ladder(thresholds.conflicts) == [
        ("good", "le", 0),
        ("warn", "lt", 0.01),
        ("bad", None, None),
    ]
    assert (thresholds.short.unit, ladder(thresholds.short)) == (
        "records",
        [("good", "le", 0), ("warn", None, None)],
    )


async def test_get_overview_gives_an_example_for_each_problem(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단용", DIAGNOSIS_ROWS)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    examples = overview.examples
    duplicate = examples.duplicate
    assert duplicate is not None
    # 중복 무리는 둘이다. 해시가 작은 무리가 보기로 나온다(데이터 탭의 첫 무리와 같다).
    first_duplicate = min(["안녕하세요", "배송 언제 와요"], key=make_text_hash)
    copies = {"안녕하세요": ("인사", 3), "배송 언제 와요": ("배송", 2)}
    assert duplicate.text == first_duplicate
    assert (duplicate.label_name, duplicate.copies) == copies[first_duplicate]
    conflict = examples.conflict
    assert conflict is not None
    assert conflict.text == "오늘 날씨 좋네요"
    assert [(label.name, label.count) for label in conflict.labels] == [("인사", 1), ("환불", 1)]
    assert conflict.labels[0].label_id == made.labels["인사"].id
    assert [(short.text, short.length) for short in examples.short] == [("hi", 2)]


async def test_get_overview_examples_show_first_two_short_texts(db_session, make_dataset):
    # 준비
    rows = [
        Row("가", "인사"),
        Row("충분히 긴 문장", "인사"),
        Row("나다", "인사", exclude_reason="manual"),
        Row("라마바", "인사"),
        Row("사아", "인사"),
    ]
    made = await make_dataset("짧은 문장", rows)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    # 학습에서 뺀 '나다'는 건너뛰고, 번호 순으로 앞의 두 건만 보인다.
    shorts = [(short.record_id, short.text, short.length) for short in overview.examples.short]
    assert shorts == [(made.records[0].id, "가", 1), (made.records[3].id, "라마바", 3)]


async def test_get_overview_examples_skip_excluded_and_trashed_records(db_session, make_dataset):
    # 준비
    rows = [
        Row("같은 말입니다", "가"),
        Row("같은 말입니다", "가", exclude_reason="manual"),
        Row("다른 라벨 문장", "가"),
        Row("다른 라벨 문장", "나", trashed=True),
        Row("짧음", "나", exclude_reason="manual"),
    ]
    made = await make_dataset("빼고 보기", rows)

    # 실행
    overview = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 확인
    examples = overview.examples
    assert (examples.duplicate, examples.conflict) == (None, None)
    assert examples.short == []


async def test_get_overview_fails_when_dataset_is_missing(db_session):
    # 실행
    with pytest.raises(NotFoundError):
        await overview_service.get_overview(db_session, dataset_id=999)


# ---------- 문장 거르기 ----------


async def test_list_records_filters_by_status(db_session, make_dataset):
    # 준비
    made = await make_dataset("상태", DIAGNOSIS_ROWS)
    dataset_id = made.dataset.id

    # 실행
    active = await _list(db_session, dataset_id)
    included = await _list(db_session, dataset_id, status="included")
    excluded = await _list(db_session, dataset_id, status="excluded")
    trash = await _list(db_session, dataset_id, status="trash")

    # 확인
    assert (len(active), len(included)) == (12, 11)
    assert excluded == ["뺀 문장입니다"]
    assert trash == ["휴지통 문장"]


async def test_list_records_filters_by_label(db_session, make_dataset):
    # 준비
    made = await make_dataset("라벨", DIAGNOSIS_ROWS)

    # 실행
    texts = await _list(db_session, made.dataset.id, label_id=made.labels["환불"].id)

    # 확인
    # 휴지통의 '휴지통 문장'(환불)은 active에 없다.
    assert texts == ["환불해 주세요", "환불 가능한가요", "오늘 날씨 좋네요"]


async def test_list_records_filters_by_problem_and_keeps_groups_together(db_session, make_dataset):
    # 준비
    made = await make_dataset("문제", DIAGNOSIS_ROWS)
    dataset_id = made.dataset.id

    # 실행
    duplicates = await _list(db_session, dataset_id, status="included", problem="duplicate")
    conflicts = await _list(db_session, dataset_id, problem="conflict")
    short = await _list(db_session, dataset_id, problem="short")

    # 확인
    # 중복은 문장·라벨이 모두 같은 것만. 라벨이 다른 같은 문장은 충돌에 나온다.
    assert sorted(duplicates) == sorted(["안녕하세요"] * 3 + ["배송 언제 와요"] * 2)
    assert conflicts == ["오늘 날씨 좋네요", "오늘 날씨 좋네요"]
    assert short == ["hi"]


async def test_list_records_keeps_groups_together_when_filtering_by_problem(
    db_session, make_dataset
):
    # 준비
    made = await make_dataset(
        "무리",
        [
            Row("가 문장", "가"),
            Row("나 문장", "가"),
            Row("가 문장", "가"),
            Row("나 문장", "가"),
            Row("가 문장", "가"),
        ],
    )

    # 실행
    duplicates = await _list(db_session, made.dataset.id, problem="duplicate")

    # 확인
    # 같은 문장끼리 붙어 나오므로, 이어진 덩어리 수가 무리 수(2)와 같다.
    assert len([text for text, _run in itertools.groupby(duplicates)]) == 2


async def test_list_records_same_as_shows_only_exactly_same_text(db_session, make_dataset):
    # 준비
    made = await make_dataset(
        "같은 문장",
        [
            Row("국무회의", "정치"),
            Row("국무회의  ", "정치"),
            Row("국무회의 참석", "정치"),
            Row("오늘 국무회의서 의결", "정치"),
        ],
    )

    # 실행
    same = await _list(db_session, made.dataset.id, same_as=made.records[0].id)

    # 확인
    # 글자가 들어 있기만 한 문장('국무회의 참석')은 빠지고, 공백만 다른 문장은 같은 문장이다.
    assert same == ["국무회의", "국무회의  "]


async def test_list_records_same_as_ignores_record_of_other_dataset(db_session, make_dataset):
    # 준비
    mine = await make_dataset("내 것", [Row("안녕하세요", "인사")])
    other = await make_dataset("남의 것", [Row("안녕하세요", "인사")])

    # 실행
    same = await _list(db_session, mine.dataset.id, same_as=other.records[0].id)

    # 확인
    assert same == []


async def test_list_records_search_treats_percent_and_underscore_as_letters(
    db_session, make_dataset
):
    # 준비
    made = await make_dataset(
        "검색",
        [Row("100% 환불", "환불"), Row("100 환불", "환불"), Row("a_b", "기타"), Row("axb", "기타")],
    )

    # 실행
    percent = await _list(db_session, made.dataset.id, q="%")
    underscore = await _list(db_session, made.dataset.id, q="_")
    upper_case = await _list(db_session, made.dataset.id, q="A_B")

    # 확인
    assert percent == ["100% 환불"]
    assert underscore == ["a_b"]
    assert upper_case == ["a_b"]


async def test_list_records_reports_duplicates_and_conflicts_per_record(db_session, make_dataset):
    # 준비
    made = await make_dataset("무리", DIAGNOSIS_ROWS)

    # 실행
    page = await records_service.list_records(
        db_session,
        dataset_id=made.dataset.id,
        status="active",
        label_id=None,
        problem=None,
        q="오늘 날씨",
        same_as=None,
        limit=10,
        offset=0,
    )

    # 확인
    assert page.total == 2
    assert [(item.duplicate_count, item.has_conflict) for item in page.items] == [
        (1, True),
        (1, True),
    ]


async def test_list_records_pages_with_limit_and_offset(db_session, make_dataset):
    # 준비
    made = await make_dataset("쪽", DIAGNOSIS_ROWS)

    # 실행
    page = await records_service.list_records(
        db_session,
        dataset_id=made.dataset.id,
        status="active",
        label_id=None,
        problem=None,
        q=None,
        same_as=None,
        limit=5,
        offset=10,
    )

    # 확인
    assert page.total == 12
    assert [item.record.id for item in page.items] == [made.records[10].id, made.records[11].id]


# ---------- 하나 고치기 ----------


async def test_update_record_changes_text_and_bumps_version(db_session, make_dataset):
    # 준비
    made = await make_dataset("고치기", [Row("안녕 하세요", "인사")])
    record_id = made.records[0].id

    # 실행
    item = await records_service.update_record(
        db_session, record_id=record_id, data=RecordUpdate(text="  안녕하세요  ", row_version=1)
    )

    # 확인
    assert item.record.text == "안녕하세요"
    assert item.record.text_hash == make_text_hash("안녕하세요")
    assert item.record.row_version == 2


async def test_update_record_fails_when_row_version_is_stale(db_session, make_dataset):
    # 준비
    made = await make_dataset("고치기", [Row("안녕하세요", "인사")])
    record_id = made.records[0].id
    await records_service.update_record(
        db_session, record_id=record_id, data=RecordUpdate(text="안녕", row_version=1)
    )

    # 실행
    with pytest.raises(ConflictError) as caught:
        await records_service.update_record(
            db_session, record_id=record_id, data=RecordUpdate(text="반가워요", row_version=1)
        )

    # 확인
    assert caught.value.message == "다른 곳에서 먼저 고쳤습니다. 새로 불러오세요."


async def test_update_record_fails_when_label_belongs_to_other_dataset(db_session, make_dataset):
    # 준비
    mine = await make_dataset("내 것", [Row("안녕하세요", "인사")])
    other = await make_dataset("남의 것", [Row("환불", "환불")])

    # 실행
    with pytest.raises(InvalidInputError):
        await records_service.update_record(
            db_session,
            record_id=mine.records[0].id,
            data=RecordUpdate(label_id=other.labels["환불"].id, row_version=1),
        )


# ---------- 일괄 작업 ----------


async def test_bulk_update_runs_each_action_and_counts_real_changes(db_session, make_dataset):
    # 준비
    made = await make_dataset(
        "일괄", [Row("하나", "가"), Row("둘", "가"), Row("셋", "나")], extra_labels=("다",)
    )
    dataset_id = made.dataset.id
    ids = [record.id for record in made.records]

    async def run(action: str, **values: object) -> int:
        data = RecordBulkUpdate(record_ids=ids, action=action, **values)
        return await records_service.bulk_update(db_session, dataset_id=dataset_id, data=data)

    # 실행
    excluded = await run("exclude")
    excluded_again = await run("exclude")
    included = await run("include")
    trashed = await run("trash")
    restored = await run("restore")
    relabeled = await run("set_label", label_id=made.labels["가"].id)

    # 확인
    assert (excluded, excluded_again, included, trashed, restored) == (3, 0, 3, 3, 3)
    assert relabeled == 1
    record = await records_service.get_record(db_session, record_id=ids[2])
    assert record.label_id == made.labels["가"].id
    assert record.row_version == 1 + 5


async def test_bulk_update_ignores_records_of_other_datasets(db_session, make_dataset):
    # 준비
    mine = await make_dataset("내 것", [Row("하나", "가")])
    other = await make_dataset("남의 것", [Row("둘", "가")])

    # 실행
    changed = await records_service.bulk_update(
        db_session,
        dataset_id=mine.dataset.id,
        data=RecordBulkUpdate(record_ids=[other.records[0].id], action="trash"),
    )

    # 확인
    assert changed == 0


async def test_bulk_update_fails_when_label_is_missing_for_set_label(db_session, make_dataset):
    # 준비
    made = await make_dataset("일괄", [Row("하나", "가")])

    # 실행
    with pytest.raises(InvalidInputError):
        await records_service.bulk_update(
            db_session,
            dataset_id=made.dataset.id,
            data=RecordBulkUpdate(record_ids=[made.records[0].id], action="set_label"),
        )


# ---------- 중복 정리 ----------


async def test_cleanup_duplicates_keeps_smallest_id_per_text_and_label(db_session, make_dataset):
    # 준비
    made = await make_dataset(
        "중복",
        [
            Row("안녕 하세요", "인사"),
            Row("안녕 하세요", "인사"),
            Row("안녕  하세요", "인사"),
            Row("안녕 하세요", "환불"),
            Row("하나뿐", "인사"),
        ],
    )

    # 실행
    changed = await records_service.cleanup_duplicates(db_session, dataset_id=made.dataset.id)

    # 확인
    excluded = await _list(db_session, made.dataset.id, status="excluded")
    kept = await _list(db_session, made.dataset.id, status="included")
    # 공백 수만 다른 "안녕  하세요"도 같은 문장이다. 라벨이 다른 "안녕 하세요"(환불)는 남는다.
    assert changed == 2
    assert excluded == ["안녕 하세요", "안녕  하세요"]
    assert kept == ["안녕 하세요", "안녕 하세요", "하나뿐"]


async def test_cleanup_duplicates_removes_what_overview_counts(db_session, make_dataset):
    # 준비
    made = await make_dataset("진단과 정리", DIAGNOSIS_ROWS)
    before = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)

    # 실행
    changed = await records_service.cleanup_duplicates(db_session, dataset_id=made.dataset.id)

    # 확인
    # 진단이 '빠질 수'라고 한 만큼만 빠지고, 정리한 뒤에는 중복이 남지 않는다.
    after = await overview_service.get_overview(db_session, dataset_id=made.dataset.id)
    assert changed == before.duplicates.extra_records
    assert (after.duplicates.extra_records, after.duplicates.grade) == (0, "good")
    assert after.conflicts.records == before.conflicts.records


async def test_undo_duplicate_cleanup_restores_only_duplicate_exclusions(db_session, make_dataset):
    # 준비
    made = await make_dataset(
        "되돌리기",
        [
            Row("같은 문장", "가"),
            Row("같은 문장", "가"),
            Row("같은 문장", "가"),
            Row("직접 뺀 문장", "가", exclude_reason="manual"),
        ],
    )
    await records_service.cleanup_duplicates(db_session, dataset_id=made.dataset.id)

    # 실행
    restored = await records_service.undo_duplicate_cleanup(db_session, dataset_id=made.dataset.id)

    # 확인
    excluded = await _list(db_session, made.dataset.id, status="excluded")
    assert restored == 2
    assert excluded == ["직접 뺀 문장"]


async def test_get_dataset_detail_counts_labels(db_session, make_dataset):
    # 준비
    made = await make_dataset("자세히", DIAGNOSIS_ROWS)

    # 실행
    detail = await classification_service.get_dataset_detail(db_session, dataset_id=made.dataset.id)

    # 확인
    summary = detail.summary
    assert (summary.record_count, summary.included_count) == (12, 11)
    assert (summary.excluded_count, summary.trash_count, summary.label_count) == (1, 1, 3)
    assert [(label.label.name, label.record_count) for label in detail.labels] == [
        ("배송", 2),
        ("인사", 6),
        ("환불", 3),
    ]


# ---------- 같은 이름 (동시에 들어온 요청) ----------


async def _name_is_free(*_args: object, **_kwargs: object) -> bool:
    """미리 확인에서 '비어 있다'고 답한다. 두 요청이 동시에 확인을 통과한 경우를 흉내 낸다."""
    return False


async def test_create_label_fails_with_conflict_when_unique_constraint_catches_race(
    db_session, make_dataset, monkeypatch
):
    # 준비
    made = await make_dataset("라벨", [Row("안녕하세요", "인사")])
    monkeypatch.setattr(classification_service, "_label_name_taken", _name_is_free)

    # 실행
    with pytest.raises(ConflictError) as caught:
        await classification_service.create_label(
            db_session, dataset_id=made.dataset.id, data=LabelCreate(name="인사")
        )

    # 확인
    assert caught.value.message == "같은 이름의 라벨이 이미 있습니다."


async def test_update_label_fails_with_conflict_when_unique_constraint_catches_race(
    db_session, make_dataset, monkeypatch
):
    # 준비
    made = await make_dataset("라벨", [Row("안녕하세요", "인사"), Row("환불요", "환불")])
    monkeypatch.setattr(classification_service, "_label_name_taken", _name_is_free)

    # 실행
    with pytest.raises(ConflictError):
        await classification_service.update_label(
            db_session, label_id=made.labels["환불"].id, data=LabelUpdate(name="인사")
        )


async def test_create_import_fails_with_conflict_when_unique_constraint_catches_race(
    db_session, make_dataset, monkeypatch
):
    # 준비
    await make_dataset("고객 문의", [Row("안녕하세요", "인사")])
    monkeypatch.setattr(datasets_service, "_name_taken", _name_is_free)
    data = ImportCreate(
        new_dataset_name="고객 문의",
        source="huggingface",
        repo="klue/klue",
        mapping={"text": "title", "label": "label"},
    )

    # 실행
    with pytest.raises(ConflictError) as caught:
        await classification_service.create_import(db_session, data=data)

    # 확인
    assert caught.value.message == "같은 이름의 데이터셋이 이미 있습니다."


# ---------- 필드 맞춤 짐작 ----------


def test_suggest_mapping_finds_columns_by_common_names():
    # 실행
    english = mapping_service.suggest_mapping(["text", "label"])
    korean = mapping_service.suggest_mapping(["문장", "라벨", "분할"])
    intent = mapping_service.suggest_mapping(["문장", "의도", "번호"])

    # 확인
    assert (english.text, english.label) == ("text", "label")
    assert (korean.text, korean.label) == ("문장", "라벨")
    assert (intent.text, intent.label) == ("문장", "의도")


def test_suggest_mapping_prefers_earlier_names_and_ignores_case():
    # 실행
    mapping = mapping_service.suggest_mapping(["Title", "TEXT", "Category", "Label", "Split"])

    # 확인
    # text가 title보다, label이 category보다 앞선 이름이다. 대소문자는 가리지 않는다.
    assert (mapping.text, mapping.label) == ("TEXT", "Label")
