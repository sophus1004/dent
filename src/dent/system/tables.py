"""표 읽기: csv·tsv·xlsx·Parquet를 (줄 번호, {열 이름: 값}) 줄로 읽고, 칸 값을 JSON에 담을 모양으로 맞춘다.

올린 파일(uploads.py)과 허깅페이스(huggingface.py)가 같은 칸 값 규칙을 쓴다.
미리 보기와 가져오기가 같은 읽기 함수(open_table)를 쓰므로 화면에서 본 줄과 실제로 들어가는 줄이 어긋나지 않는다.

csv는 첫 줄이 열 이름이다. 글자 인코딩은 UTF-8(BOM 포함)인지 먼저 보고, 아니면 CP949로 읽는다.
칸 구분자는 첫 줄에서 쉼표·탭·세미콜론 가운데 가장 많이 나온 것으로 고른다.
따옴표가 닫히지 않은 줄은 말없이 뒷줄을 삼키지 않고 오류로 알린다(strict). 다만 tsv는 따옴표를
글자 그대로 쓰는 파일이 많아서(예: 따옴표로 시작하는 리뷰), 따옴표 규칙으로 읽히지 않으면 따옴표를
풀지 않고 다시 읽는다.
"""

import codecs
import csv
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from python_calamine import CalamineError, CalamineSheet, CalamineWorkbook

from dent.system.exceptions import InvalidInputError, NotFoundError
from dent.system.schemas import CellValue

# 받는 확장자와 형식. tsv는 구분자만 다른 csv로 읽는다.
FORMAT_BY_SUFFIX = {".csv": "csv", ".tsv": "csv", ".xlsx": "xlsx"}

UNSUPPORTED_FORMAT_MESSAGE = "지원하지 않는 형식입니다. .csv 또는 .xlsx 파일을 올려 주세요."

# 파일을 읽는 조각 크기. 큰 파일도 메모리에 한 번에 올리지 않는다.
CHUNK_BYTES = 1024 * 1024

# 알아보는 칸 구분자. 첫 줄에서 같은 수로 나오면 앞의 것을 고른다.
DELIMITERS = (",", "\t", ";")

# csv 칸 하나의 최대 글자 수. 기본값(13만 자)보다 긴 칸도 읽어서 모듈이 '너무 긴 문장'으로 건너뛰게 한다.
CSV_FIELD_SIZE_LIMIT = 16 * 1024 * 1024

# 화면에 보여 주는 인코딩 이름 → 파이썬이 읽을 때 쓰는 이름. utf-8-sig는 BOM이 있든 없든 읽는다.
PYTHON_ENCODING = {"utf-8": "utf-8-sig", "cp949": "cp949"}

# Parquet 파일을 한 번에 읽는 줄 수. 수백만 줄 파일도 메모리에 한 번에 올리지 않는다.
PARQUET_BATCH_ROWS = 10_000

# 이름이 빈 열에 붙이는 이름의 앞부분. 예: 세 번째 열이 비어 있으면 "열3"
UNNAMED_COLUMN_PREFIX = "열"

# PostgreSQL 문자열에는 NUL 글자를 넣을 수 없다.
NUL = "\x00"


@dataclass
class FileTable:
    """읽을 준비가 된 표 하나. rows는 첫 줄(열 이름) 다음부터 (줄 번호, {열 이름: 값})을 낸다."""

    # 형식: csv · xlsx
    format: str

    # 열 이름들
    columns: list[str]

    # 데이터 줄 수 (열 이름 줄과 빈 줄 제외)
    row_count: int

    # (줄 번호, {열 이름: 값}). 줄 번호는 csv면 파일 줄, 엑셀이면 시트 행 번호다.
    rows: Iterator[tuple[int, dict[str, CellValue]]]

    # 글자 인코딩 (csv)
    encoding: str | None = None

    # 칸 구분자 (csv)
    delimiter: str | None = None

    # 시트 이름들 (xlsx)
    sheets: list[str] = field(default_factory=list)

    # 읽은 시트 (xlsx)
    sheet: str | None = None


def open_table(path: Path, *, sheet: str | None) -> FileTable:
    """파일을 표로 연다. 읽을 수 없으면 InvalidInputError, 시트가 없으면 NotFoundError."""
    file_format = FORMAT_BY_SUFFIX.get(path.suffix.lower())
    if file_format == "csv":
        return _open_csv(path)
    if file_format == "xlsx":
        return _open_xlsx(path, sheet=sheet)
    raise InvalidInputError(UNSUPPORTED_FORMAT_MESSAGE)


# ---------- 칸 값 ----------


def unique_columns(header: list[Any]) -> list[str]:
    """첫 줄 값으로 열 이름을 만든다. 빈 이름은 '열N', 겹치는 이름은 뒤에 _2, _3을 붙인다."""
    columns: list[str] = []
    for position, raw in enumerate(header, start=1):
        name = to_text(cell_value(raw)) or f"{UNNAMED_COLUMN_PREFIX}{position}"
        candidate = name
        copy_number = 2
        while candidate in columns:
            candidate = f"{name}_{copy_number}"
            copy_number += 1
        columns.append(candidate)
    return columns


def cell_value(value: Any) -> CellValue:
    """원본 칸 값을 JSON에 담을 수 있는 값(문자열·숫자·null)으로 바꾼다."""
    if value is None:
        return None
    # bool은 int의 자식이라 숫자보다 먼저 본다.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN·무한대는 JSON에 담을 수 없다. 엑셀은 정수도 3.0처럼 주므로 정수로 되돌린다.
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        cleaned = value.replace(NUL, "")
        return cleaned or None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, default=str).replace(NUL, "")
    return str(value).replace(NUL, "")


def to_text(value: CellValue) -> str:
    """칸 값을 앞뒤 공백을 지운 문자열로 바꾼다. 비었으면 빈 문자열."""
    if value is None:
        return ""
    return str(value).strip()


# ---------- Parquet ----------


def parquet_columns(path: Path) -> list[str]:
    """Parquet 파일의 열 이름들."""
    return list(pq.ParquetFile(path).schema_arrow.names)


def parquet_row_count(path: Path) -> int:
    """Parquet 파일의 줄 수. 파일 머리의 정보만 읽는다."""
    return pq.ParquetFile(path).metadata.num_rows


def iter_parquet_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Parquet 파일의 줄을 {열 이름: 원래 값}으로 조금씩 낸다. 칸 값 바꾸기는 부른 쪽이 한다."""
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=PARQUET_BATCH_ROWS):
        yield from batch.to_pylist()


# ---------- csv ----------


def _open_csv(path: Path) -> FileTable:
    """csv(tsv)를 연다. 인코딩·구분자·따옴표 규칙을 알아낸 뒤 줄 수를 센다."""
    encoding = _detect_encoding(path)
    is_tsv = path.suffix.lower() == ".tsv"
    delimiter = "\t" if is_tsv else _detect_delimiter(path, encoding=encoding)
    quoting, line_count = _count_csv_lines(
        path, encoding=encoding, delimiter=delimiter, is_tsv=is_tsv
    )
    if line_count == 0:
        raise InvalidInputError("파일이 비어 있습니다.")

    lines = _csv_lines(path, encoding=encoding, delimiter=delimiter, quoting=quoting)
    columns = unique_columns(next(lines)[1])
    rows = _csv_lines(path, encoding=encoding, delimiter=delimiter, quoting=quoting)
    return FileTable(
        format="csv",
        columns=columns,
        row_count=line_count - 1,
        rows=_rows_after_header(rows, columns),
        encoding=encoding,
        delimiter=delimiter,
    )


def _count_csv_lines(path: Path, *, encoding: str, delimiter: str, is_tsv: bool) -> tuple[int, int]:
    """따옴표 규칙을 정하고 빈 줄을 뺀 줄 수(첫 줄 포함)를 센다. (따옴표 규칙, 줄 수)를 돌려준다."""
    try:
        lines = _csv_lines(path, encoding=encoding, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        return csv.QUOTE_MINIMAL, sum(1 for _ in lines)
    except InvalidInputError:
        if not is_tsv:
            raise
    # tsv는 따옴표를 글자 그대로 쓰는 파일이 많다. 따옴표를 풀지 않고 다시 센다.
    lines = _csv_lines(path, encoding=encoding, delimiter=delimiter, quoting=csv.QUOTE_NONE)
    return csv.QUOTE_NONE, sum(1 for _ in lines)


def _detect_encoding(path: Path) -> str:
    """UTF-8(BOM 포함)인지, CP949인지 알아낸다. 둘 다 아니면 InvalidInputError."""
    with path.open("rb") as file:
        has_bom = file.read(len(codecs.BOM_UTF8)) == codecs.BOM_UTF8
    # BOM이 있어도 파일 전체를 확인한다. 뒤쪽에 깨진 글자가 있으면 읽다가 멈추기 때문이다.
    if _decodes_as(path, PYTHON_ENCODING["utf-8"]):
        return "utf-8"
    # BOM이 있으면 UTF-8로 쓰려던 파일이다. CP949로 읽으면 글자가 모두 깨진다.
    if not has_bom and _decodes_as(path, PYTHON_ENCODING["cp949"]):
        return "cp949"
    raise InvalidInputError(
        "글자 인코딩을 알 수 없습니다. UTF-8이나 CP949(EUC-KR)로 저장해서 다시 올려 주세요."
    )


def _decodes_as(path: Path, encoding: str) -> bool:
    """파일 전체가 이 인코딩으로 읽히는지 본다. 앞부분만 보면 뒤에서 깨진 글자를 놓친다."""
    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        with path.open("rb") as file:
            while chunk := file.read(CHUNK_BYTES):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _detect_delimiter(path: Path, *, encoding: str) -> str:
    """비어 있지 않은 첫 줄에서 가장 많이 나온 구분자를 고른다. 하나도 없으면 쉼표(열이 하나)."""
    first_line = ""
    with path.open(encoding=PYTHON_ENCODING[encoding], newline="") as file:
        for line in file:
            if line.strip():
                first_line = line
                break
    best = max(DELIMITERS, key=first_line.count)
    return best if first_line.count(best) > 0 else ","


def _csv_lines(
    path: Path, *, encoding: str, delimiter: str, quoting: int
) -> Iterator[tuple[int, list[str]]]:
    """빈 줄을 뺀 (시작 줄 번호, 칸들)을 차례로 낸다. 따옴표 안의 줄바꿈은 한 줄로 친다.

    읽을 수 없는 줄을 만나면 InvalidInputError. quoting은 csv.QUOTE_MINIMAL(따옴표를 푼다)이나
    csv.QUOTE_NONE(따옴표를 글자 그대로 둔다)이다.
    """
    csv.field_size_limit(CSV_FIELD_SIZE_LIMIT)
    with path.open(encoding=PYTHON_ENCODING[encoding], newline="") as file:
        # strict: 닫히지 않은 따옴표를 만나면 뒷줄을 한 칸에 삼키지 않고 오류를 낸다.
        reader = csv.reader(file, delimiter=delimiter, quoting=quoting, strict=True)
        last_line = 0
        try:
            for cells in reader:
                start_line = last_line + 1
                last_line = reader.line_num
                if not _is_empty_row(cells):
                    yield start_line, cells
        except csv.Error:
            raise InvalidInputError(
                f"파일 {last_line + 1}번째 줄 근처를 읽지 못했습니다. "
                "따옴표가 제대로 닫혔는지 확인하세요."
            ) from None
        except UnicodeDecodeError:
            raise InvalidInputError(
                f"파일 {last_line + 1}번째 줄 근처에 읽을 수 없는 글자가 있습니다. "
                "UTF-8이나 CP949(EUC-KR)로 다시 저장해서 올려 주세요."
            ) from None


# ---------- xlsx ----------


def _open_xlsx(path: Path, *, sheet: str | None) -> FileTable:
    """엑셀 파일의 시트 하나를 연다. 시트를 주지 않으면 첫 시트."""
    try:
        with CalamineWorkbook.from_path(str(path)) as workbook:
            sheets = list(workbook.sheet_names)
            chosen = sheet or (sheets[0] if sheets else "")
            if chosen not in sheets:
                raise NotFoundError(f"'{chosen}' 시트가 없습니다.")
            # 시트 내용은 통째로 메모리에 올라오므로, 파일을 닫은 뒤에도 읽을 수 있다.
            data = workbook.get_sheet_by_name(chosen)
    except CalamineError:
        raise InvalidInputError(
            "엑셀 파일을 읽지 못했습니다. "
            "파일이 깨지지 않았는지, 암호가 걸려 있지 않은지 확인하세요."
        ) from None

    first = next(_sheet_lines(data), None)
    if first is None:
        raise InvalidInputError(f"'{chosen}' 시트가 비어 있습니다.")
    columns = unique_columns(first[1])
    line_count = sum(1 for _ in _sheet_lines(data))
    return FileTable(
        format="xlsx",
        columns=columns,
        row_count=line_count - 1,
        rows=_rows_after_header(_sheet_lines(data), columns),
        sheets=sheets,
        sheet=chosen,
    )


def _sheet_lines(data: CalamineSheet) -> Iterator[tuple[int, list[Any]]]:
    """빈 행을 뺀 (시트 행 번호, 칸들)을 차례로 낸다. iter_rows는 늘 시트의 1행부터 낸다."""
    for row_number, cells in enumerate(data.iter_rows(), start=1):
        if not _is_empty_row(cells):
            yield row_number, cells


# ---------- 공통 ----------


def _rows_after_header(
    lines: Iterator[tuple[int, list[Any]]], columns: list[str]
) -> Iterator[tuple[int, dict[str, CellValue]]]:
    """첫 줄(열 이름)을 건너뛰고, 나머지 줄을 {열 이름: 값}으로 바꿔 낸다. 모자란 칸은 None."""
    next(lines, None)
    for line, cells in lines:
        values = {
            name: cell_value(cells[index] if index < len(cells) else None)
            for index, name in enumerate(columns)
        }
        yield line, values


def _is_empty_row(cells: list[Any]) -> bool:
    """모든 칸이 비었거나 공백뿐인지."""
    return all(to_text(cell_value(cell)) == "" for cell in cells)
