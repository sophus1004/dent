"""내보내기 (흐름의 끝): 학습 · 평가 · 코퍼스 파일을 만든다. 파일 자리 · 기록 · 내려받기 · 보관은 시스템(system/exports.py)이 한다.

파일 모양
- train_jsonl: bge-m3(FlagEmbedding) 학습 줄 {"query", "pos": [...], "neg": [...]} (+ "pos_scores", "neg_scores")
  · 출처 칸 "source"(학습기는 모르는 칸을 읽지 않는다).
  오답은 풀 전체를 넣는다(학습기가 매 에폭 train_group_size - 1개를 무작위로 고른다).
- train_table: sentence-transformers 표(csv) anchor · positive · negative_1 … negative_n (정답마다 한 줄, 가장 하드한 n개)
  · 출처 칸 source(고를 수 있다. sentence-transformers에 바로 넣을 때는 이 칸을 빼고 넣는다).
- beir: zip — corpus.jsonl · queries.jsonl(metadata.source) · qrels/test.tsv(모든 질의).
  정답의 중복 묶음(같은 본문 · 근접 중복)도 정답으로 넣는다(같은 내용을 가져와도 맞은 것으로).
- corpus: 운영 색인에 넣을 zip — corpus.jsonl(학습 글) · rules.json(머리말 규칙 · 뗀 반복 구간).
  학습 글과 색인 글이 같아야 해서, 학습 파일과 같은 규칙으로 만든 글과 그 규칙을 함께 낸다.
문서 글은 모두 학습 글(머리말 + 떼기로 고른 구간을 뺀 본문)이다. BEIR은 title을 비우고 text에 학습 글을 둔다
(평가 도구가 제목을 두 번 붙이지 않게).
train · valid · test로 나누지 않는다. 학습에 쓰는 질의(학습 제외 · 휴지통 뺌)를 모두 넣고, 출처(original · synthetic · human)
칸으로 AI가 만든 질의를 가를 수 있게 한다.
넣을 출처(원본 · 찾기 · 합성 · 도우미 · 사람)의 판정만 쓴다. 오답은 기준 검색에서 가까운 것(하드)부터.
교사 점수(기본 켬)는 판정의 Jev 확률을 로짓(log p/(1-p))으로 넣고, 없는 것은 Jev에 물어 채운다(Jev가 없으면 실패).
FlagEmbedding은 교사 점수에 온도 없이 softmax를 씌우므로 0~1 확률을 그대로 넣으면 목표가 거의 평평해진다.
심각한 검사가 남았으면 학습 · 평가 파일은 내보내지 않는다(흐름의 내보내기 칸이 잠김). 코퍼스는 언제든 낸다.
작업 실행기는 작업을 하나씩 처리하므로, 교사 점수를 채울 학습 파일(Jev에 한 판정씩 묻는다)은 다른 파일 뒤에 넣는다
(빠른 평가 · 코퍼스 파일이 그 뒤에서 기다리지 않게). 미리 보기는 가장 최근에 잰 교사 점수 속도로 걸릴 시간을 어림한다.
"""

import csv
import io
import itertools
import json
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import judging
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    POSITIVE_MIN_GRADE,
    Document,
    Judgment,
    Query,
    Ranking,
    Repeat,
    RepeatDecision,
)
from dent.modules.retrieval.schemas import ExportCreate, ExportFileRead, ExportPreviewRead
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, DOCUMENT_INPUT, QUERY_INCLUDED
from dent.modules.retrieval.text_rules import HEADER_SEPARATOR
from dent.system import connections, jobs
from dent.system import exports as exports_service
from dent.system.exceptions import ConflictError, InvalidInputError
from dent.system.models import ConnectionRole, Export, Job, JobStatus

# 파일 모양별 파일 이름 끝
FILE_SUFFIX = {
    "train_jsonl": "train.jsonl",
    "train_table": "train.csv",
    "beir": "eval-beir.zip",
    "corpus": "corpus.zip",
}

# 학습 파일 모양 (학습 질의가 있어야 한다)
TRAIN_FORMATS = ("train_jsonl", "train_table")

# 교사 점수를 로짓으로 바꿀 때 확률을 자르는 끝 (0 · 1이면 로짓이 끝없이 커진다)
TEACHER_EPSILON = 1e-4

# 코퍼스 규칙 파일의 판
RULES_VERSION = 1

# 같은 정답 문서를 쓰는 학습 질의가 이 몫 이상이고 학습 질의가 이 수보다 적으면 배치 경고
# (FlagEmbedding은 한 배치 안의 같은 문서를 가리지 않아 서로의 정답이 오답이 된다)
SHARED_POSITIVE_WARN_SHARE = 0.3
SHARED_POSITIVE_WARN_BELOW = 10_000

# 미리 보기 한 줄에서 문서 글을 줄이는 글자 수와 오답을 보이는 수
PREVIEW_TEXT_LENGTH = 60
PREVIEW_NEGATIVES = 2

# 교사 점수: Jev에 물을 때 진행률을 적는 간격
TEACHER_PROGRESS_EVERY = 50

# 교사 점수 시간을 어림할 때 거슬러 볼 최근 내보내기 작업 수 (교사 점수 단계가 있던 것을 찾는다)
TEACHER_SPEED_LOOKBACK = 20

# 작업 단계 이름
PHASE_TEACHER = "teacher"
PHASE_WRITING = "writing"

EXPORT_LOCKED_MESSAGE = "심각한 검사가 남아 내보낼 수 없습니다({names}). 먼저 고쳐 주세요."
NO_JEV_WARNING = "teacher_no_jev"
SHARED_POSITIVE_WARNING = "shared_positive"
NO_JEV_MESSAGE = "교사 점수를 넣으려면 Jev를 연결하세요."
NO_TRAIN_MESSAGE = "내보낼 질의가 없습니다."
# 출처 칸 이름 (학습 jsonl · 학습 표 · BEIR queries의 metadata)
SOURCE_COLUMN = "source"


@dataclass
class Pack:
    """내보낼 질의 · 판정을 모은 것."""

    # 질의 번호 → (글, 출처 original · synthetic · human)
    queries: dict[int, tuple[str, str]] = field(default_factory=dict)

    # 질의 번호 → 정답 [(문서 번호, 등급, 교사 점수)] · 오답 풀 [(문서 번호, 교사 점수)] (하드한 것부터)
    positives: dict[int, list[tuple[int, int, float | None]]] = field(default_factory=dict)
    negatives: dict[int, list[tuple[int, float | None]]] = field(default_factory=dict)

    # 문서 번호 → 학습 글
    documents: dict[int, str] = field(default_factory=dict)

    # 문서 번호 → 중복 묶음 대표 (평가 파일을 만들 때만)
    group_of: dict[int, int] = field(default_factory=dict)

    def query_ids(self) -> list[int]:
        """정답이 있는 질의 번호 (번호 순)."""
        return [qid for qid in self.queries if self.positives.get(qid)]


async def preview(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> ExportPreviewRead:
    """내보내기 미리 보기: 고른 대로 만든 첫 줄 · 파일마다 수 · 출처별 판정 수 · 남은 주의."""
    dataset = await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    pack = await _collect(db, dataset_id=dataset_id, data=data)
    wanted = data.negatives
    query_ids = pack.query_ids()
    negative_counts = [len(pack.negatives.get(qid, [])) for qid in query_ids]
    files = [
        _file_facts(pack, fmt, file_name=_file_name(dataset.name, fmt), wanted=wanted)
        for fmt in data.formats
    ]
    source_counts = dict(
        (
            await db.execute(
                select(Judgment.source, func.count())
                .join(Query, Query.id == Judgment.query_id)
                .join(Document, Document.id == Judgment.document_id)
                .where(Judgment.dataset_id == dataset_id, QUERY_INCLUDED, DOCUMENT_ACTIVE)
                .group_by(Judgment.source)
            )
        )
        .tuples()
        .all()
    )
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    warnings = {
        check.key: check.view_count or 1 for check in overview.checks if check.grade == "warn"
    }
    shared = _shared_positive_queries(pack)
    is_small = len(query_ids) < SHARED_POSITIVE_WARN_BELOW
    if query_ids and is_small and shared / len(query_ids) >= SHARED_POSITIVE_WARN_SHARE:
        warnings[SHARED_POSITIVE_WARNING] = shared
    teacher_missing = len(_teacher_missing(pack)) if data.teacher_scores else 0
    per_pair = await _teacher_seconds_per_pair(db) if teacher_missing else None
    has_jev = await connections.get_connection(db, role=ConnectionRole.JEV) is not None
    if teacher_missing and not has_jev:
        warnings[NO_JEV_WARNING] = teacher_missing
    blocked = [check.key for check in overview.checks if check.grade == "bad"]
    return ExportPreviewRead(
        line=_preview_line(pack, data=data),
        files=files,
        source_counts={str(key): int(value) for key, value in source_counts.items()},
        lacking=sum(1 for count in negative_counts if count < wanted),
        average_negatives=(sum(negative_counts) / len(negative_counts)) if negative_counts else 0.0,
        teacher_missing=teacher_missing,
        teacher_seconds=per_pair * teacher_missing if per_pair is not None else None,
        warnings=warnings,
        blocked=blocked,
    )


async def create_exports(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> list[Export]:
    """고른 파일 모양마다 내보내기 기록과 작업을 넣는다.

    학습 파일 · BEIR은 심각이 남았으면 ConflictError(코퍼스만 고르면 막지 않는다).
    교사 점수를 고르고 학습 파일을 내는데 Jev가 없으면 InvalidInputError.
    """
    dataset = await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    is_corpus_only = set(data.formats) == {"corpus"}
    if not is_corpus_only:
        overview = await overview_service.get_overview(db, dataset_id=dataset_id)
        bad = [
            overview_service.CHECK_NAMES.get(check.key, check.key)
            for check in overview.checks
            if check.grade == "bad"
        ]
        if bad:
            raise ConflictError(EXPORT_LOCKED_MESSAGE.format(names=" · ".join(bad)))
    has_train = any(fmt in TRAIN_FORMATS for fmt in data.formats)
    if (
        data.teacher_scores
        and has_train
        and await connections.get_connection(db, role=ConnectionRole.JEV) is None
    ):
        raise InvalidInputError(NO_JEV_MESSAGE)
    # 교사 점수를 채울 학습 파일은 뒤로(같은 무리 안에서는 고른 순서 그대로).
    fills_teacher = {fmt: data.teacher_scores and fmt in TRAIN_FORMATS for fmt in data.formats}
    made = []
    for fmt in sorted(data.formats, key=lambda fmt: fills_teacher[fmt]):
        export = await exports_service.create_export(
            db,
            module=retrieval_service.MODULE_NAME,
            dataset_id=dataset_id,
            format=fmt,
            file_name=_file_name(dataset.name, fmt),
            options=data.model_dump(mode="json"),
        )
        made.append(export)
    await db.commit()
    return [await exports_service.get_export(db, export_id=export.id) for export in made]


async def build_export(
    db: AsyncSession,
    *,
    export_id: int,
    job_id: int,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """내보낼 파일 하나를 만든다(작업 실행기). 실패하면 도메인 예외를 낸다(부른 쪽이 fail_export)."""
    export = await exports_service.get_export(db, export_id=export_id)
    exports_service.start_export(export)
    await db.commit()
    data = ExportCreate.model_validate(export.options)
    path = exports_service.file_path(export)
    if export.format == "corpus":
        await jobs.start_phase(db, job_id=job_id, phase=PHASE_WRITING, total=None)
        await db.commit()
        result = await _write_corpus(db, dataset_id=export.dataset_id, path=path)
        await exports_service.finish_export(db, export, job_id=job_id, result=result)
        await db.commit()
        return
    pack = await _collect(db, dataset_id=export.dataset_id, data=data)
    is_train = export.format in TRAIN_FORMATS
    if is_train and not pack.query_ids():
        raise InvalidInputError(NO_TRAIN_MESSAGE)
    if data.teacher_scores and is_train:
        await _fill_teacher_scores(db, pack=pack, job_id=job_id, transport=jev_transport)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_WRITING, total=None)
    await db.commit()
    if export.format == "train_jsonl":
        result = _write_jsonl(pack, path=path, data=data)
    elif export.format == "train_table":
        result = _write_table(pack, path=path, data=data)
    else:
        result = _write_beir(pack, path=path)
    await exports_service.finish_export(db, export, job_id=job_id, result=result)
    await db.commit()


# ---------- 모으기 ----------


async def _collect(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> Pack:
    """학습에 쓰는 질의와 고른 출처의 판정, 쓰는 문서(학습 글)를 모은다. 오답은 기준 검색에서 가까운 것부터."""
    pack = Pack()
    sources = [source.value for source in data.sources]
    for query_id, text, source in await db.execute(
        select(Query.id, Query.text, Query.source)
        .where(Query.dataset_id == dataset_id, QUERY_INCLUDED)
        .order_by(Query.id)
    ):
        pack.queries[query_id] = (text, source)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    similarity: dict[tuple[int, int], float] = {}
    if analysis is not None:
        similarity = {
            (query_id, document_id): value
            for query_id, document_id, value in await db.execute(
                select(Ranking.query_id, Ranking.document_id, Ranking.similarity).where(
                    Ranking.analysis_id == analysis.id
                )
            )
        }
    negatives: dict[int, list[tuple[int, float | None]]] = defaultdict(list)
    for query_id, document_id, grade, teacher in await db.execute(
        select(Judgment.query_id, Judgment.document_id, Judgment.grade, Judgment.teacher_score)
        .join(Document, Document.id == Judgment.document_id)
        .where(Judgment.dataset_id == dataset_id, DOCUMENT_ACTIVE, Judgment.source.in_(sources))
        .order_by(Judgment.query_id, Judgment.document_id)
    ):
        if query_id not in pack.queries:
            continue
        if grade >= POSITIVE_MIN_GRADE:
            pack.positives.setdefault(query_id, []).append((document_id, grade, teacher))
        else:
            negatives[query_id].append((document_id, teacher))
    for query_id, items in negatives.items():
        # 가까운 것(하드)부터, 같은 유사도면 문서 번호 순으로 한 번 더 정렬해 파일이 늘 같게 한다.
        items.sort(key=lambda item: (-similarity.get((query_id, item[0]), 0.0), item[0]))
        pack.negatives[query_id] = items
    used = {doc for items in pack.positives.values() for doc, _g, _t in items} | {
        doc for items in pack.negatives.values() for doc, _t in items
    }
    has_beir = "beir" in data.formats
    # 평가 · 코퍼스 파일은 코퍼스 전체를 쓰고, 학습 파일은 쓰는 문서만 읽는다.
    needs_all = has_beir or "corpus" in data.formats
    query = select(Document.id, DOCUMENT_INPUT).where(
        Document.dataset_id == dataset_id, DOCUMENT_ACTIVE
    )
    if not needs_all:
        query = query.where(Document.id.in_(used or {-1}))
    for document_id, text in await db.execute(query):
        pack.documents[document_id] = text
    if has_beir:
        pack.group_of = await retrieval_service.duplicate_groups(db, dataset_id=dataset_id)
    return pack


async def _fill_teacher_scores(
    db: AsyncSession, *, pack: Pack, job_id: int, transport: httpx.AsyncBaseTransport | None
) -> None:
    """학습 질의의 판정(정답 · 오답 풀 전체) 가운데 교사 점수가 없는 것을 Jev로 채우고 판정에도 적는다."""
    connection = await connections.get_connection(db, role=ConnectionRole.JEV)
    if connection is None:
        raise InvalidInputError(NO_JEV_MESSAGE)
    missing = _teacher_missing(pack)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_TEACHER, total=len(missing))
    await db.commit()
    scores: dict[tuple[int, int], float] = {}
    for done, (query_id, document_id) in enumerate(missing, start=1):
        probability = await judging.ask_contains(
            connection,
            query=pack.queries[query_id][0],
            document=pack.documents[document_id],
            transport=transport,
        )
        if probability is not None:
            scores[(query_id, document_id)] = probability
            judgment = await db.get(Judgment, (query_id, document_id))
            if judgment is not None:
                judgment.teacher_score = probability
        if done % TEACHER_PROGRESS_EVERY == 0 or done == len(missing):
            await jobs.set_progress(db, job_id=job_id, done=done, total=len(missing))
            await db.commit()
    for query_id, items in pack.positives.items():
        pack.positives[query_id] = [
            (d, g, t if t is not None else scores.get((query_id, d))) for d, g, t in items
        ]
    for query_id, items in pack.negatives.items():
        pack.negatives[query_id] = [
            (d, t if t is not None else scores.get((query_id, d))) for d, t in items
        ]


async def _teacher_seconds_per_pair(db: AsyncSession) -> float | None:
    """가장 최근에 끝난 교사 점수 단계가 판정 하나에 쓴 시간(초). 잰 적이 없으면 None.

    교사 점수 단계의 시작부터 다음 단계(쓰기)의 시작까지를 그 단계의 판정 수로 나눈다(작업의 사건 목록).
    """
    finished = await db.scalars(
        select(Job)
        .where(
            Job.module == retrieval_service.MODULE_NAME,
            Job.kind == exports_service.EXPORT_JOB_KIND,
            Job.status == JobStatus.DONE,
        )
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .limit(TEACHER_SPEED_LOOKBACK)
    )
    for job in finished:
        phases = [event for event in job.events or [] if event.get("type") == jobs.EVENT_PHASE]
        for current, following in itertools.pairwise(phases):
            total = current.get("total")
            if current.get("phase") == PHASE_TEACHER and total:
                elapsed = datetime.fromisoformat(following["at"]) - datetime.fromisoformat(
                    current["at"]
                )
                return elapsed.total_seconds() / total
    return None


def _teacher_missing(pack: Pack) -> list[tuple[int, int]]:
    """교사 점수가 없는 학습 판정 (질의, 문서)."""
    return [
        (qid, doc)
        for qid in pack.query_ids()
        for doc, score in [
            *((d, t) for d, _g, t in pack.positives.get(qid, [])),
            *pack.negatives.get(qid, []),
        ]
        if score is None
    ]


def _shared_positive_queries(pack: Pack) -> int:
    """정답 문서를 다른 질의와 같이 쓰는 질의 수 (같은 청크로 만든 질의 등)."""
    users: dict[int, int] = {}
    for qid in pack.query_ids():
        for doc, _g, _t in pack.positives.get(qid, []):
            users[doc] = users.get(doc, 0) + 1
    return sum(
        1
        for qid in pack.query_ids()
        if any(users[doc] > 1 for doc, _g, _t in pack.positives.get(qid, []))
    )


# ---------- 쓰기 ----------


def teacher_logit(probability: float) -> float:
    """Jev 확률 → 교사 점수(로짓). FlagEmbedding이 온도 없이 softmax를 씌우므로 로짓으로 넣는다."""
    clipped = min(max(probability, TEACHER_EPSILON), 1 - TEACHER_EPSILON)
    return math.log(clipped / (1 - clipped))


def _train_line(pack: Pack, query_id: int, *, data: ExportCreate) -> dict[str, Any]:
    positives = pack.positives.get(query_id, [])
    negatives = pack.negatives.get(query_id, [])
    line: dict[str, Any] = {
        "query": pack.queries[query_id][0],
        "pos": [pack.documents[doc] for doc, _g, _t in positives],
        "neg": [pack.documents[doc] for doc, _t in negatives],
    }
    if data.teacher_scores:
        line["pos_scores"] = [
            round(teacher_logit(t), 4) if t is not None else None for _d, _g, t in positives
        ]
        line["neg_scores"] = [
            round(teacher_logit(t), 4) if t is not None else None for _d, t in negatives
        ]
    line[SOURCE_COLUMN] = pack.queries[query_id][1]
    return line


def _write_jsonl(pack: Pack, *, path: Any, data: ExportCreate) -> dict[str, Any]:
    counts = {"queries": 0, "positives": 0, "negatives": 0}
    with path.open("w", encoding="utf-8") as file:
        for query_id in pack.query_ids():
            line = _train_line(pack, query_id, data=data)
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
            counts["queries"] += 1
            counts["positives"] += len(line["pos"])
            counts["negatives"] += len(line["neg"])
    return counts


def _write_table(pack: Pack, *, path: Any, data: ExportCreate) -> dict[str, Any]:
    wanted = data.negatives
    counts = {"queries": 0, "rows": 0}
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        source_head = [SOURCE_COLUMN] if data.source_column else []
        writer.writerow(
            [
                "anchor",
                "positive",
                *[f"negative_{index}" for index in range(1, wanted + 1)],
                *source_head,
            ]
        )
        for query_id in pack.query_ids():
            text, source = pack.queries[query_id]
            negatives = [
                pack.documents[doc] for doc, _t in pack.negatives.get(query_id, [])[:wanted]
            ]
            negatives += [""] * (wanted - len(negatives))
            source_cell = [source] if data.source_column else []
            for doc, _g, _t in pack.positives.get(query_id, []):
                writer.writerow([text, pack.documents[doc], *negatives, *source_cell])
                counts["rows"] += 1
            counts["queries"] += 1
    return counts


def _write_beir(pack: Pack, *, path: Any) -> dict[str, Any]:
    query_ids = pack.query_ids()
    members: dict[int, list[int]] = {}
    for document_id in pack.documents:
        members.setdefault(pack.group_of.get(document_id, document_id), []).append(document_id)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        corpus = io.StringIO()
        for document_id, text in pack.documents.items():
            # 제목은 학습 글 머리말에 들어 있어 title을 비운다(평가 도구가 두 번 붙이지 않게).
            corpus.write(
                json.dumps({"_id": str(document_id), "title": "", "text": text}, ensure_ascii=False)
                + "\n"
            )
        archive.writestr("corpus.jsonl", corpus.getvalue())
        queries = io.StringIO()
        for query_id in query_ids:
            text, source = pack.queries[query_id]
            line = {"_id": str(query_id), "text": text, "metadata": {SOURCE_COLUMN: source}}
            queries.write(json.dumps(line, ensure_ascii=False) + "\n")
        archive.writestr("queries.jsonl", queries.getvalue())
        # train · valid · test로 나누지 않으므로 모든 질의를 BEIR의 기본 이름(test) 하나에 둔다
        lines = ["query-id\tcorpus-id\tscore"]
        for query_id in query_ids:
            grades: dict[int, int] = {}
            for doc, grade, _t in pack.positives.get(query_id, []):
                # 정답의 중복 묶음도 같은 등급의 정답으로 (같은 내용을 가져와도 맞은 것)
                for member in members.get(pack.group_of.get(doc, doc), [doc]):
                    grades[member] = max(grades.get(member, 0), grade)
            for doc, grade in sorted(grades.items()):
                lines.append(f"{query_id}\t{doc}\t{grade}")
        if len(lines) > 1:
            archive.writestr("qrels/test.tsv", "\n".join(lines) + "\n")
    return {"queries": len(query_ids), "documents": len(pack.documents), "qrels": len(lines) - 1}


async def _write_corpus(db: AsyncSession, *, dataset_id: int, path: Any) -> dict[str, Any]:
    """코퍼스 zip: corpus.jsonl(모든 코퍼스 문서의 학습 글) · rules.json(학습 글을 만든 규칙)."""
    settings = await retrieval_service.get_settings(db, dataset_id=dataset_id)
    repeats = list(
        await db.scalars(
            select(Repeat)
            .where(Repeat.dataset_id == dataset_id)
            .order_by(Repeat.kind, Repeat.document_count.desc(), Repeat.id)
        )
    )

    def rule(repeat: Repeat) -> dict[str, Any]:
        return {
            "kind": repeat.kind,
            "text": repeat.text,
            "samples": repeat.samples,
            "documents": repeat.document_count,
        }

    rules = {
        "version": RULES_VERSION,
        "text": "머리말 + 본문 (떼기로 고른 반복 구간을 뺀 것)",
        "header": HEADER_SEPARATOR.join(["제목", "머리말 칸", "구획 경로"]),
        "separator": HEADER_SEPARATOR,
        "section_header": settings.section_header,
        "doc_max_tokens": settings.doc_max_tokens,
        "removed": [rule(r) for r in repeats if r.decision == RepeatDecision.REMOVE.value],
        "kept": [rule(r) for r in repeats if r.decision == RepeatDecision.KEEP.value],
        "undecided": sum(1 for r in repeats if r.decision is None),
    }
    count = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        corpus = io.StringIO()
        for document_id, doc_key, title, header, section, text, body in await db.execute(
            select(
                Document.id,
                Document.doc_key,
                Document.title,
                Document.header,
                Document.section,
                DOCUMENT_INPUT,
                Document.text,
            )
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
            .order_by(Document.id)
        ):
            line = {
                "_id": str(document_id),
                "doc_key": doc_key,
                "text": text,
                "body": body,
                "metadata": {"title": title, "header": header, "section": section},
            }
            corpus.write(json.dumps(line, ensure_ascii=False) + "\n")
            count += 1
        archive.writestr("corpus.jsonl", corpus.getvalue())
        archive.writestr("rules.json", json.dumps(rules, ensure_ascii=False, indent=2))
    return {"documents": count, "removed_rules": len(rules["removed"])}


# ---------- 미리 보기 ----------


def _file_name(dataset_name: str, fmt: str) -> str:
    return exports_service.safe_file_name(f"{dataset_name}-{FILE_SUFFIX[fmt]}")


def _file_facts(pack: Pack, fmt: str, *, file_name: str, wanted: int) -> ExportFileRead:
    if fmt == "corpus":
        return ExportFileRead(
            format=fmt,
            file_name=file_name,
            queries=0,
            positives=0,
            negatives=0,
            documents=len(pack.documents),
        )
    ids = pack.query_ids()
    if fmt == "beir":
        return ExportFileRead(
            format=fmt,
            file_name=file_name,
            queries=len(ids),
            positives=sum(len(pack.positives.get(qid, [])) for qid in ids),
            negatives=0,
            documents=len(pack.documents),
        )
    # jsonl은 오답 풀 전체, 표는 칸 수(wanted)만큼
    per_query = (
        (lambda qid: len(pack.negatives.get(qid, [])))
        if fmt == "train_jsonl"
        else (lambda qid: min(len(pack.negatives.get(qid, [])), wanted))
    )
    return ExportFileRead(
        format=fmt,
        file_name=file_name,
        queries=len(ids),
        positives=sum(len(pack.positives.get(qid, [])) for qid in ids),
        negatives=sum(per_query(qid) for qid in ids),
        documents=0,
    )


def _preview_line(pack: Pack, *, data: ExportCreate) -> str:
    """학습 jsonl의 첫 줄(글은 줄여서)."""
    ids = pack.query_ids()
    if not ids:
        return "—"
    line = _train_line(pack, ids[0], data=data)
    short = {
        **line,
        "pos": [_clip(text) for text in line["pos"]],
        "neg": [_clip(text) for text in line["neg"][:PREVIEW_NEGATIVES]]
        + (
            [f"…{len(line['neg']) - PREVIEW_NEGATIVES}개 더"]
            if len(line["neg"]) > PREVIEW_NEGATIVES
            else []
        ),
    }
    return json.dumps(short, ensure_ascii=False)


def _clip(text: str) -> str:
    return text if len(text) <= PREVIEW_TEXT_LENGTH else text[:PREVIEW_TEXT_LENGTH] + "…"
