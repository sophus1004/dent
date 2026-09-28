"""표 읽기 테스트 (DB 없이): 칸 값 맞추기, 열 이름, Parquet 읽기."""

import math
from datetime import date

import pytest

from dent.system import tables
from dent.system.exceptions import InvalidInputError
from tests.system.helpers import parquet_bytes


def test_cell_value_makes_values_fit_in_json():
    # 실행
    values = [
        tables.cell_value(True),
        tables.cell_value(3.0),
        tables.cell_value(2.5),
        tables.cell_value(math.nan),
        tables.cell_value("a\x00b"),
        tables.cell_value(""),
        tables.cell_value(date(2026, 9, 24)),
        tables.cell_value(["가", 1]),
    ]

    # 확인
    # 엑셀은 정수도 3.0처럼 주고, PostgreSQL 문자열에는 NUL을 넣을 수 없다.
    assert values == ["true", 3, 2.5, None, "ab", None, "2026-09-24", '["가", 1]']


def test_unique_columns_names_empty_and_repeated_headers():
    # 실행
    columns = tables.unique_columns(["text", None, "text", " ", "text"])

    # 확인
    assert columns == ["text", "열2", "text_2", "열4", "text_3"]


def test_to_text_strips_spaces_and_turns_none_into_empty_text():
    # 실행
    texts = [tables.to_text("  안녕  "), tables.to_text(None), tables.to_text(7)]

    # 확인
    assert texts == ["안녕", "", "7"]


def test_parquet_reading_gives_columns_row_count_and_raw_rows(tmp_path):
    # 준비
    path = tmp_path / "data.parquet"
    path.write_bytes(parquet_bytes(["좋아요", "별로예요"], [1, 0]))

    # 실행
    columns = tables.parquet_columns(path)
    row_count = tables.parquet_row_count(path)
    rows = list(tables.iter_parquet_rows(path))

    # 확인
    assert columns == ["text", "label"]
    assert row_count == 2
    assert rows == [{"text": "좋아요", "label": 1}, {"text": "별로예요", "label": 0}]


def test_open_table_fails_when_format_is_not_supported(tmp_path):
    # 준비
    path = tmp_path / "memo.txt"
    path.write_text("hello", encoding="utf-8")

    # 실행
    with pytest.raises(InvalidInputError) as caught:
        tables.open_table(path, sheet=None)

    # 확인
    assert caught.value.message == tables.UNSUPPORTED_FORMAT_MESSAGE
