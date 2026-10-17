"""분류 내보내기: 학습 jsonl · 학습 표(csv) · 라벨 목록(json)을 만든다.

파일 자리 · 기록 · 내려받기 · 보관은 시스템(system/exports.py)이 한다.

파일 모양
- train_jsonl: 한 줄에 {"text", "label", "label_text", "source", "label_source", "group"}. label은 라벨 목록의 번호(0부터),
  label_text는 라벨 이름이다(SetFit · 허깅페이스 datasets가 쓰는 칸 이름).
- train_table: csv text · label · label_text (+ source · label_source · group, 고를 수 있다).
- labels: json {"id2label": {"0": 이름}, "label2id": {이름: 0}} (transformers 모델 설정에 그대로 넣는다).
라벨 번호는 학습에 쓰는 문장이 있는 라벨에 라벨 번호(만든 순)대로 0부터 매긴다. 넣을 출처를 골라도 번호는
바뀌지 않아, 따로 만든 파일끼리도 번호가 같다.
train · valid · test로 나누지 않는다. 학습에 쓰는 문장(학습 제외 · 휴지통 · 라벨 없음은 뺌)을 모두 넣고,
출처 칸(original · synthetic)으로 도우미가 만든 새 문장을, 라벨 출처 칸(original · helper)으로 도우미가 바꾼
라벨을 가를 수 있게 한다(학습 · 평가에서 AI가 손댄 것을 빼거나 따로 볼 수 있게).
묶음 칸(group)은 같은 글이거나 가장 최근 뜻 분석의 근접 중복 쌍으로 이어진 문장들의 대표 번호(가장 작은 문장 번호)다.
받은 쪽이 학습 · 평가로 나눌 때 묶음 단위로 나누면(GroupShuffleSplit 등) 거의 같은 문장이 양쪽에 들어가지 않는다.
심각이 남아도 막지 않는다. 미리 보기가 남은 심각 · 주의를 보인다.
"""

import csv
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, get_args

from sqlalchemy import ColumnElement, and_, case, exists, func, not_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import helper_tools, records
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import (
    HelperChange,
    HelperChangeField,
    Label,
    NearDuplicate,
    Record,
)
from dent.modules.classification.schemas import (
    ExportCheckRead,
    ExportCreate,
    ExportFileRead,
    ExportPreviewRead,
    RecordSource,
)
from dent.modules.classification.service import IS_EXCLUDED, IS_INCLUDED, IS_SYNTHETIC
from dent.system import exports as exports_service
from dent.system import jobs
from dent.system.exceptions import InvalidInputError
from dent.system.groups import Groups
from dent.system.models import Export

# 파일 모양별 파일 이름 끝
FILE_SUFFIX = {
    "train_jsonl": "train.jsonl",
    "train_table": "train.csv",
    "labels": "labels.json",
}

# 학습 파일 모양 (문장이 있어야 한다)
TRAIN_FORMATS = ("train_jsonl", "train_table")

# 출처 칸 이름과 값
SOURCE_COLUMN = "source"
SOURCE_ORIGINAL = "original"
SOURCE_SYNTHETIC = "synthetic"

# 라벨 출처 칸 이름과 값 (도우미가 바꾼 라벨이면 helper)
LABEL_SOURCE_COLUMN = "label_source"
LABEL_SOURCE_HELPER = "helper"

# 묶음 칸 이름 (같은 글 · 근접 중복으로 이어진 문장들의 대표 번호)
GROUP_COLUMN = "group"

# 파일을 쓸 때 DB에서 한 번에 받는 줄 수 (수백만 건을 한꺼번에 메모리에 올리지 않게)
STREAM_BATCH = 5_000

# 미리 보기 한 줄에서 문장을 줄이는 글자 수
PREVIEW_TEXT_LENGTH = 80

# 작업 단계 이름
PHASE_WRITING = "writing"

NO_RECORDS_MESSAGE = "내보낼 문장이 없습니다. 학습에 쓰는 문장과 넣을 출처를 확인하세요."

# 문장의 출처 (도우미가 만든 새 문장이면 synthetic)
SOURCE_OF_RECORD = case((IS_SYNTHETIC, SOURCE_SYNTHETIC), else_=SOURCE_ORIGINAL)

# 도우미가 바꾼 라벨인지: 되돌리지 않은 라벨 바꾸기 기록이 있고 지금 라벨이 그 바꾼 뒤 라벨이다
# (사람이 다시 바꿨으면 지금 라벨이 달라 원본으로 본다).
IS_LABEL_BY_HELPER = exists().where(
    HelperChange.record_id == Record.id,
    HelperChange.field == HelperChangeField.LABEL.value,
    HelperChange.undone_at.is_(None),
    HelperChange.after == func.to_jsonb(Record.label_id),
)
LABEL_SOURCE_OF_RECORD = case((IS_LABEL_BY_HELPER, LABEL_SOURCE_HELPER), else_=SOURCE_ORIGINAL)

# 내보낼 수 있는 문장: 학습에 쓰고 라벨이 있는 것
IS_EXPORTABLE = and_(IS_INCLUDED, Record.label_id.is_not(None))


async def preview(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> ExportPreviewRead:
    """내보내기 미리 보기: 첫 줄 · 파일마다 수 · 출처별 문장 수 · 빠지는 문장 · 남은 심각 · 주의."""
    dataset = await classification_service.get_dataset(db, dataset_id=dataset_id)
    labels = await _label_numbers(db, dataset_id=dataset_id)
    source_counts = await _source_counts(db, dataset_id=dataset_id)
    records = sum(source_counts.get(source, 0) for source in data.sources)
    files = [
        ExportFileRead(
            format=fmt,
            file_name=_file_name(dataset.name, fmt),
            records=records if fmt in TRAIN_FORMATS else 0,
            labels=len(labels),
        )
        for fmt in data.formats
    ]
    excluded, unlabeled = (
        await db.execute(
            select(
                func.count().filter(IS_EXCLUDED),
                func.count().filter(IS_INCLUDED, Record.label_id.is_(None)),
            ).where(Record.dataset_id == dataset_id)
        )
    ).one()
    measures = await helper_tools.measure_all(db, dataset_id=dataset_id)
    checks = [
        ExportCheckRead(
            key=key,
            name=helper_tools.CHECK_TITLES[key],
            value=measure.text,
            grade=measure.grade,  # type: ignore[arg-type]
        )
        for key, measure in measures.items()
        if measure.grade != "good"
    ]
    # 심각 먼저 (같으면 검사 순서)
    checks.sort(key=lambda check: check.grade != "bad")
    return ExportPreviewRead(
        line=await _preview_line(db, dataset_id=dataset_id, data=data, labels=labels),
        files=files,
        source_counts=source_counts,
        excluded=int(excluded),
        unlabeled=int(unlabeled),
        checks=checks,
    )


async def create_exports(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> list[Export]:
    """고른 파일 모양마다 내보내기 기록과 작업을 넣는다. 넣을 문장이 없으면 InvalidInputError."""
    dataset = await classification_service.get_dataset(db, dataset_id=dataset_id)
    source_counts = await _source_counts(db, dataset_id=dataset_id)
    has_records = any(source_counts.get(source, 0) for source in data.sources)
    if not has_records:
        raise InvalidInputError(NO_RECORDS_MESSAGE)
    made = []
    for fmt in data.formats:
        export = await exports_service.create_export(
            db,
            module=classification_service.MODULE_NAME,
            dataset_id=dataset_id,
            format=fmt,
            file_name=_file_name(dataset.name, fmt),
            options=data.model_dump(mode="json"),
        )
        made.append(export)
    await db.commit()
    return [await exports_service.get_export(db, export_id=export.id) for export in made]


async def build_export(db: AsyncSession, *, export_id: int, job_id: int) -> None:
    """내보낼 파일 하나를 만든다(작업 실행기). 실패하면 도메인 예외를 낸다(부른 쪽이 fail_export)."""
    export = await exports_service.get_export(db, export_id=export_id)
    exports_service.start_export(export)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_WRITING, total=None)
    await db.commit()
    data = ExportCreate.model_validate(export.options)
    labels = await _label_numbers(db, dataset_id=export.dataset_id)
    if not labels:
        raise InvalidInputError(NO_RECORDS_MESSAGE)
    path = exports_service.file_path(export)
    if export.format == "labels":
        result = _write_labels(labels, path=path)
    else:
        groups = await group_numbers(db, dataset_id=export.dataset_id)
        rows = _rows(db, dataset_id=export.dataset_id, sources=data.sources, groups=groups)
        if export.format == "train_jsonl":
            result = await _write_jsonl(rows, labels=labels, path=path)
        else:
            result = await _write_table(
                rows, labels=labels, path=path, source_column=data.source_column
            )
        if not result["records"]:
            raise InvalidInputError(NO_RECORDS_MESSAGE)
    await exports_service.finish_export(db, export, job_id=job_id, result=result)
    await db.commit()


# ---------- 모으기 ----------


async def _label_numbers(db: AsyncSession, *, dataset_id: int) -> dict[int, tuple[int, str]]:
    """학습에 쓰는 문장이 있는 라벨마다 {라벨 번호: (내보낼 번호 0부터, 이름)}. 라벨 번호(만든 순)대로."""
    has_record = exists().where(Record.label_id == Label.id, IS_INCLUDED)
    rows = await db.execute(
        select(Label.id, Label.name)
        .where(Label.dataset_id == dataset_id, has_record)
        .order_by(Label.id)
    )
    return {label_id: (number, name) for number, (label_id, name) in enumerate(rows)}


async def _source_counts(db: AsyncSession, *, dataset_id: int) -> dict[str, int]:
    """출처마다 내보낼 수 있는 문장 수. 없는 출처도 0으로 싣는다."""
    rows = await db.execute(
        select(SOURCE_OF_RECORD, func.count())
        .where(Record.dataset_id == dataset_id, IS_EXPORTABLE)
        .group_by(SOURCE_OF_RECORD)
    )
    counts = {source: 0 for source in get_args(RecordSource)}
    counts.update({str(source): int(count) for source, count in rows})
    return counts


def _source_filter(sources: list[str]) -> ColumnElement[bool]:
    """넣을 출처만 고르는 조건. 둘 다 고르면 거르지 않는다."""
    wants_original = SOURCE_ORIGINAL in sources
    wants_synthetic = SOURCE_SYNTHETIC in sources
    if wants_original and wants_synthetic:
        return true()
    return IS_SYNTHETIC if wants_synthetic else not_(IS_SYNTHETIC)


def _rows_query(dataset_id: int, sources: list[str]) -> Any:
    # 번호 순으로 한 번 더 정렬해 파일이 늘 같게 한다.
    return (
        select(Record.id, Record.text, Record.label_id, SOURCE_OF_RECORD, LABEL_SOURCE_OF_RECORD)
        .where(Record.dataset_id == dataset_id, IS_EXPORTABLE, _source_filter(sources))
        .order_by(Record.id)
    )


async def group_numbers(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """묶음에 들 수 있는 내보낼 문장마다 {문장 번호: 묶음 대표 번호}. 싣지 않은 문장은 혼자다(대표가 자기 번호).

    같은 글(해시)이거나 가장 최근 뜻 분석의 근접 중복 쌍이면 한 묶음이다(이어진 쌍도). 수백만 건이어도
    묶음에 드는 문장만 읽는다.
    """
    pairs = (
        await db.execute(
            select(NearDuplicate.text_hash_a, NearDuplicate.text_hash_b).where(
                NearDuplicate.map_id == records.done_map_id(dataset_id)
            )
        )
    ).all()
    repeated = (
        select(Record.text_hash)
        .where(Record.dataset_id == dataset_id, IS_EXPORTABLE)
        .group_by(Record.text_hash)
        .having(func.count() > 1)
    )
    paired = {text_hash for pair in pairs for text_hash in pair}
    grouped = Groups[str]()
    first_of: dict[str, int] = {}
    members: list[tuple[int, str]] = []
    for record_id, text_hash in await db.execute(
        select(Record.id, Record.text_hash)
        .where(
            Record.dataset_id == dataset_id,
            IS_EXPORTABLE,
            Record.text_hash.in_(paired) | Record.text_hash.in_(repeated),
        )
        .order_by(Record.id)
    ):
        first_of.setdefault(text_hash, record_id)
        grouped.add(text_hash)
        members.append((record_id, text_hash))
    for text_hash_a, text_hash_b in pairs:
        if text_hash_a in grouped and text_hash_b in grouped:
            grouped.union(text_hash_a, text_hash_b)
    # 묶음의 대표 번호: 묶음에 든 해시들의 첫 문장 번호 가운데 가장 작은 것
    lowest: dict[str, int] = {}
    for text_hash, record_id in first_of.items():
        root = grouped.find(text_hash)
        lowest[root] = min(lowest.get(root, record_id), record_id)
    return {record_id: lowest[grouped.find(text_hash)] for record_id, text_hash in members}


# 내보낼 문장 한 줄: (글, 라벨 번호, 출처, 라벨 출처, 묶음 대표 번호)
Row = tuple[str, int, str, str, int]


async def _rows(
    db: AsyncSession, *, dataset_id: int, sources: list[str], groups: dict[int, int]
) -> AsyncIterator[Row]:
    """내보낼 문장 (글, 라벨 번호, 출처, 라벨 출처, 묶음)을 번호 순으로 묶음마다 받아 하나씩 낸다."""
    result = await db.stream(
        _rows_query(dataset_id, sources).execution_options(yield_per=STREAM_BATCH)
    )
    async for record_id, text, label_id, source, label_source in result:
        yield text, label_id, source, label_source, groups.get(record_id, record_id)


# ---------- 쓰기 ----------


def _line(
    text: str, *, label: tuple[int, str], source: str, label_source: str, group: int
) -> dict[str, Any]:
    number, name = label
    return {
        "text": text,
        "label": number,
        "label_text": name,
        SOURCE_COLUMN: source,
        LABEL_SOURCE_COLUMN: label_source,
        GROUP_COLUMN: group,
    }


async def _write_jsonl(
    rows: AsyncIterator[Row], *, labels: dict[int, tuple[int, str]], path: Path
) -> dict[str, Any]:
    count = 0
    # 줄바꿈은 OS와 상관없이 \n이다(Windows에서 \r\n이 되지 않게: 같은 데이터면 어느 PC에서나 같은 파일).
    with path.open("w", encoding="utf-8", newline="\n") as file:
        async for text, label_id, source, label_source, group in rows:
            line = _line(
                text,
                label=labels[label_id],
                source=source,
                label_source=label_source,
                group=group,
            )
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
            count += 1
    return {"records": count, "labels": len(labels)}


async def _write_table(
    rows: AsyncIterator[Row],
    *,
    labels: dict[int, tuple[int, str]],
    path: Path,
    source_column: bool,
) -> dict[str, Any]:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        source_columns = [SOURCE_COLUMN, LABEL_SOURCE_COLUMN, GROUP_COLUMN] if source_column else []
        writer.writerow(["text", "label", "label_text", *source_columns])
        async for text, label_id, source, label_source, group in rows:
            number, name = labels[label_id]
            sources = [source, label_source, group] if source_column else []
            writer.writerow([text, number, name, *sources])
            count += 1
    return {"records": count, "labels": len(labels)}


def _write_labels(labels: dict[int, tuple[int, str]], *, path: Path) -> dict[str, Any]:
    ordered = sorted(labels.values())
    content = {
        # transformers 설정처럼 번호 열쇠는 글자로 둔다(JSON 열쇠는 글자뿐이다).
        "id2label": {str(number): name for number, name in ordered},
        "label2id": {name: number for number, name in ordered},
    }
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return {"records": 0, "labels": len(labels)}


# ---------- 미리 보기 ----------


def _file_name(dataset_name: str, fmt: str) -> str:
    return exports_service.safe_file_name(f"{dataset_name}-{FILE_SUFFIX[fmt]}")


async def _preview_line(
    db: AsyncSession,
    *,
    dataset_id: int,
    data: ExportCreate,
    labels: dict[int, tuple[int, str]],
) -> str:
    """학습 jsonl의 첫 줄(문장은 줄여서)."""
    first = (await db.execute(_rows_query(dataset_id, data.sources).limit(1))).first()
    if first is None:
        return "—"
    record_id, text, label_id, source, label_source = first
    clipped = text if len(text) <= PREVIEW_TEXT_LENGTH else text[:PREVIEW_TEXT_LENGTH] + "…"
    # 미리 보기는 묶음을 세지 않는다: 첫 줄은 번호가 가장 작아 묶음이 있어도 대개 그 대표다.
    line = _line(
        clipped,
        label=labels[label_id],
        source=source,
        label_source=label_source,
        group=record_id,
    )
    return json.dumps(line, ensure_ascii=False)
