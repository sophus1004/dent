"""분류 도우미가 끝난 뒤: 남은 것(지금 진단으로 센 줄)과 AI로 고치기의 방법 · 새 문장 만들기.

남은 것은 저장하지 않는다. 검사 가운데 통과하지 못한 것을 셋으로 나눠 그때마다 센다(사람이 고치면 줄이 줄거나 사라진다):
  ai       중복 여분(규칙), 라벨 충돌(Jev → LLM), 오라벨 의심 · 근접 중복(LLM이 라벨 고르기),
           짧은 문장(LLM이 뜻 없는 것만 뺌), 라벨 균형(새 문장)
  blocked  의미 쏠림(데이터 더하기)
고치는 함수는 도우미(helper.Helper)의 규칙 · 판정 도구를 쓰므로 helper.py에 있고, 여기에는 줄 정의 ·
새 문장 계획 · 새 문장 만들기(LLM → 그 라벨의 문장 · 앞서 더한 새 문장과 근접 거르기 → Jev)만 둔다.
train · valid · test는 나누지 않는다(한 덩어리).
"""

import itertools
import json
import math
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import helper_tools, semantic_checks
from dent.modules.classification import overview as overview_service
from dent.modules.classification import records as records_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import Label, Record
from dent.system import connections, embedding, jev
from dent.system import helper as helper_service
from dent.system.agent import LEFT_AI, LEFT_BLOCKED, Agent, LeftAdd, LeftItem
from dent.system.exceptions import ExternalServiceError
from dent.system.llm import ChatMessage
from dent.system.models import Connection, ConnectionRole, HelperEventKind
from dent.system.text import make_text_hash, normalize_text

# 한 번에 보는 최대 수 (비용 어림도 이 수로 끊는다) · LLM에 한 번에 보이는 문장 수 · 그 묶음의 지시 몫(토큰)
FIX_MAX_ITEMS = 300
JUDGE_BATCH = 12
JUDGE_PROMPT_TOKENS = 300

# LLM · Jev 카드에 보일 줄 수
CARD_ROWS = 8

# 새 문장: 라벨 하나 · 한 번에 만드는 최대 수, LLM 한 번에 만드는 수, 보기 문장 수, Jev 통과 확률
MAX_GENERATE_PER_LABEL = 100
MAX_GENERATE_TOTAL = 300
GENERATE_BATCH = 20
GENERATE_SAMPLE_COUNT = 12
GENERATE_OTHER_SAMPLE_COUNT = 2
GENERATE_MIN_JEV = 0.8
GENERATE_MAX_TOKENS = 3_000
GENERATED_TEXT_MAX_LENGTH = 300

# 새 문장 거르기: 그 라벨의 학습에 쓰는 문장이나 앞서 더한 새 문장과 뜻이 이만큼 가까우면 버린다
# (근접 중복은 학습에 보탬이 적고 뜻 분석에서 다시 문제로 뜬다). 뜻 분석의 근접 중복과 같은 기준이다.
GENERATE_NEAR_SIMILARITY = semantic_checks.NEAR_DUPLICATE_MIN_SIMILARITY

# 근접 거르기에서 견주는 그 라벨 문장의 상한 (해시 순). 벡터는 뜻 분석의 캐시에서 읽고 없는 것만 서버에 묻는다.
GENERATE_NEAR_REFERENCE_MAX = 20_000

# LLM에 '이미 만든 문장'으로 보이는 수 (같은 대상 · 같은 사실을 되풀이하지 않게, 지시 토큰을 늘리지 않을 만큼)
GENERATE_MADE_SHOWN = 30

# LLM에 'Jev가 다른 라벨로 읽어 버린 문장'으로 보이는 수 (다음 묶음이 같은 쪽으로 새지 않게)
GENERATE_DROPPED_SHOWN = 10

# LLM에 묻는 횟수의 상한 = 필요한 묶음 수 × 이 배수 + 1 (거르기로 버리는 몫을 채운다. 실행 토큰 상한이 따로 막는다)
GENERATE_ROUNDS_FACTOR = 2

# 새 문장 비용 어림: 라벨마다 지시 · 보기(토큰)와 문장 하나(토큰)
GENERATE_PROMPT_TOKENS = 600
GENERATE_TOKENS_PER_TEXT = 40

LABEL_PROMPT = (
    "너는 분류 학습 데이터의 라벨 판정자다. 문장마다 라벨 목록에서 맞는 라벨 하나를 고른다. "
    "문장만으로 가를 수 없으면 unsure. "
    '답은 JSON 하나만: {"items": [{"id": 번호, "label": "라벨 이름" | "unsure"}]}'
)
SHORT_PROMPT = (
    "너는 분류 학습 데이터를 살핀다. 짧은 문장마다 라벨을 가를 뜻이 있으면 keep, "
    "없으면(한 낱말 머리말 · 인사 · 기호) drop. "
    '답은 JSON 하나만: {"items": [{"id": 번호, "answer": "keep" | "drop"}]}'
)
UNSURE = "unsure"
KEEP = "keep"
DROP = "drop"


@dataclass(frozen=True)
class FixSpec:
    """남은 것 한 줄의 묶음과 AI로 고치는 방법."""

    # 묶음 (ai · blocked)
    group: str

    # AI로 고치면 하는 일
    how: str = ""

    # 항목 하나에 드는 LLM 토큰 · Jev 호출 (어림)
    tokens_per_item: int = 0
    jev_per_item: int = 0

    # AI로 못 고칠 때 할 일
    action: str = ""


FIX_SPECS = {
    "duplicate": FixSpec(LEFT_AI, "여분 빼기 · 규칙"),
    "conflict": FixSpec(LEFT_AI, "Jev → LLM 둘째 판정 · 애매하면 학습에서 빼기", 120, 1),
    "suspect": FixSpec(
        LEFT_AI, "분류기 = Jev는 바꾸기 · 나머지 LLM이 라벨 고르기 · 애매하면 유지", 120
    ),
    "near_duplicate": FixSpec(LEFT_AI, "LLM이 라벨 맞추기 → 규칙으로 한쪽 빼기", 240),
    "short": FixSpec(LEFT_AI, "LLM이 뜻 없는 문장만 학습에서 빼기", 60),
    "balance": FixSpec(LEFT_AI, "새 문장"),
    "skew": FixSpec(LEFT_BLOCKED, action="작은 무리 쪽 데이터 더하기"),
}

# 고치는 순서 (도우미 단계와 같다. 새 문장은 맨 끝: 심각이 남으면 만들지 않는다)
FIX_ORDER = ("conflict", "suspect", "short", "duplicate", "near_duplicate", "balance")


def ordered(keys: list[str]) -> list[str]:
    """고른 검사를 고치는 순서로(모르는 것은 뒤에)."""
    rank = {key: index for index, key in enumerate(FIX_ORDER)}
    return sorted(dict.fromkeys(keys), key=lambda key: rank.get(key, len(rank)))


# ---------- 새 문장 계획 ----------


@dataclass(frozen=True)
class BalancePlan:
    """라벨 균형을 맞출 새 문장 계획.

    targets는 만들 라벨마다 (번호, 이름, 더할 수, 지금 수), rows는 문장이 있는 라벨마다
    (번호, 이름, 지금 수, 더할 수)로 사람이 고칠 표다. 목표 배율은 설정에서 읽는다.
    """

    targets: list[tuple[int, str, int, int]]
    total: int
    tokens: int
    target: float
    rows: list[tuple[int, str, int, int]]


async def balance_plan(
    db: AsyncSession, *, dataset_id: int, adds: dict[int, int] | None = None
) -> BalancePlan:
    """가장 많은 라벨 ÷ 목표 배율(설정)까지 모자란 라벨을 채우는 계획. 적은 라벨부터, 상한 안에서.

    adds(라벨 번호 → 수, 사람이 남은 것 표에서 고친 값)를 주면 계획 대신 그 수대로 만든다(상한 안에서,
    주지 않은 라벨은 0). 문장이 없는 라벨은 보기가 없어 만들지 않는다.
    """
    settings = await classification_service.get_settings(db, dataset_id=dataset_id)
    target = settings.balance_target
    counts = await helper_tools.included_label_counts(db, dataset_id=dataset_id)
    ids = {
        label.name: label.id
        for label in await db.scalars(select(Label).where(Label.dataset_id == dataset_id))
    }
    filled = {name: count for name, count in counts.items() if count > 0}
    if len(filled) < 2:
        return BalancePlan(targets=[], total=0, tokens=0, target=target, rows=[])
    wanted = math.ceil(max(filled.values()) / target)
    rows = []
    total = 0
    for name, count in sorted(filled.items(), key=lambda item: item[1]):
        if adds is None:
            add = min(wanted - count, MAX_GENERATE_PER_LABEL, MAX_GENERATE_TOTAL - total)
        else:
            add = min(adds.get(ids[name], 0), MAX_GENERATE_PER_LABEL, MAX_GENERATE_TOTAL - total)
        add = max(add, 0)
        total += add
        rows.append((ids[name], name, count, add))
    targets = [(label_id, name, add, count) for label_id, name, count, add in rows if add > 0]
    tokens = len(targets) * GENERATE_PROMPT_TOKENS + total * GENERATE_TOKENS_PER_TEXT
    return BalancePlan(targets=targets, total=total, tokens=tokens, target=target, rows=rows)


# ---------- 남은 것 ----------


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """지금 검사 가운데 통과하지 못한 것마다 한 줄 (묶음 · 방법 · 어림 비용)."""
    measures = await helper_tools.measure_all(db, dataset_id=dataset_id)
    has_jev = await connections.get_connection(db, role=ConnectionRole.JEV) is not None
    items = []
    for key, measure in measures.items():
        if measure.grade == "good":
            continue
        spec = FIX_SPECS[key]
        count = min(max(int(measure.value), 1), FIX_MAX_ITEMS)
        tokens = count * spec.tokens_per_item
        if spec.tokens_per_item:
            tokens += math.ceil(count / JUDGE_BATCH) * JUDGE_PROMPT_TOKENS
        jev_calls = count * spec.jev_per_item if has_jev else 0
        how = spec.how
        adds: list[LeftAdd] = []
        adds_note = ""
        if key == "balance":
            plan = await balance_plan(db, dataset_id=dataset_id)
            tokens = plan.tokens
            jev_calls = plan.total if has_jev else 0
            planned = " · ".join(f"{name} +{count}" for _, name, count, _ in plan.targets)
            how = f"새 문장 {planned or '없음'} · 근접 거르기 · Jev ≥ {GENERATE_MIN_JEV}"
            adds = [
                LeftAdd(key=str(label_id), name=name, now=now, add=add, max=MAX_GENERATE_PER_LABEL)
                for label_id, name, now, add in plan.rows
            ]
            adds_note = f"목표 {plan.target:g}배 · 라벨당 최대 {MAX_GENERATE_PER_LABEL}"
        value, unit = _split_unit(measure.text)
        items.append(
            LeftItem(
                key=key,
                name=helper_tools.CHECK_TITLES[key],
                value=value,
                unit=unit,
                sub=await _sub(db, dataset_id=dataset_id, key=key),
                grade=measure.grade,
                group=spec.group,
                how=how,
                tokens=tokens,
                jev=jev_calls,
                action=spec.action,
                adds=adds,
                adds_note=adds_note,
            )
        )
    return items


def _split_unit(text: str) -> tuple[str, str]:
    """'12건' → ('12', '건'), '1.8배' → ('1.8', '배')."""
    digits = len(text) - len(text.lstrip("0123456789.,"))
    return text[:digits], text[digits:]


async def _sub(db: AsyncSession, *, dataset_id: int, key: str) -> str:
    """줄의 작은 글: 라벨 균형은 가장 적은 · 많은 라벨, 나머지는 검사 이름대로."""
    if key == "balance":
        overview = await overview_service.get_overview(db, dataset_id=dataset_id)
        return f"최소 {overview.balance.min_label} · 최다 {overview.balance.max_label}"
    return {
        "conflict": "같은 문장 · 다른 라벨",
        "suspect": "분류기가 다른 라벨로 봄",
        "short": f"{records_service.SHORT_TEXT_LENGTH}자 미만",
        "duplicate": "문장 · 라벨 같음",
        "near_duplicate": "뜻이 거의 같은 쌍",
        "skew": "라벨 안에서 한 무리에 몰림",
    }.get(key, "")


# ---------- 새 문장 만들기 ----------


async def generate_sentences(
    agent: Agent,
    *,
    labels: list[Label],
    plan: BalancePlan,
    embedding_transport: httpx.AsyncBaseTransport | None,
) -> tuple[int, int]:
    """계획대로 라벨마다 새 문장을 만든다: LLM이 만들고 → 그 라벨의 문장 · 앞서 더한 새 문장과 근접한 것을 거르고
    → Jev가 라벨을 확인한다. 통과한 문장은 데이터에 더하고 변경 기록에 남긴다(되돌리면 휴지통).

    (더한 수, 근접해 버린 수).
    """
    near = await near_filter(agent, transport=embedding_transport)
    added_total = 0
    near_total = 0
    for label_id, name, count, _now in plan.targets:
        await agent.check_stop()
        added, near_rejected = await generate_label(
            agent, label_id=label_id, name=name, count=count, labels=labels, near=near
        )
        added_total += added
        near_total += near_rejected
    return added_total, near_total


@dataclass
class NearFilter:
    """새 문장이 그 라벨에 이미 있는 문장이나 앞서 더한 새 문장과 뜻이 거의 같은지 본다(임베딩이 있을 때만)."""

    # 임베딩 연결
    connection: Connection

    # 가짜 임베딩 서버 (테스트만)
    transport: httpx.AsyncBaseTransport | None

    async def units(self, texts: list[str]) -> np.ndarray:
        """문장들의 길이 1 벡터 (보내는 글은 뜻 분석과 같은 정규화한 문장)."""
        vectors = await embedding.embed_texts(
            self.connection, [normalize_text(text) for text in texts], transport=self.transport
        )
        return semantic_checks.normalized(np.asarray(vectors, dtype=np.float32))

    async def label_units(self, db: AsyncSession, *, dataset_id: int, label_id: int) -> np.ndarray:
        """라벨의 학습에 쓰는 문장 벡터(길이 1). 뜻 분석의 캐시에 있는 것은 읽고 없는 것만 서버에 묻는다.

        모델을 새로 등록하지 않는다(등록은 커밋하므로 도우미의 트랜잭션 가운데서 하지 않는다).
        """
        rows = await helper_tools.label_texts(
            db, dataset_id=dataset_id, label_id=label_id, limit=GENERATE_NEAR_REFERENCE_MAX
        )
        hashes = [text_hash for text_hash, _ in rows]
        model = await embedding.get_registered_model(db, name=self.connection.model or "")
        missing = set(hashes)
        if model is not None:
            missing = set(
                await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=hashes)
            )
        parts: list[np.ndarray] = []
        cached = [text_hash for text_hash in hashes if text_hash not in missing]
        if model is not None and cached:
            parts.append(await embedding.load_vectors(db, model=model, text_hashes=cached))
        if missing:
            parts.append(
                await self.units([text for text_hash, text in rows if text_hash in missing])
            )
        if not parts:
            return np.empty((0, 0), dtype=np.float32)
        return semantic_checks.normalized(np.vstack(parts))


def _is_near(unit: np.ndarray, *, reference: np.ndarray, made: list[np.ndarray]) -> bool:
    """벡터 하나가 reference(라벨의 문장)나 made(앞서 더한 새 문장) 가운데 하나와 기준 이상 가까운지."""
    best = float((reference @ unit).max()) if len(reference) else 0.0
    if made:
        best = max(best, float((np.stack(made) @ unit).max()))
    return best >= GENERATE_NEAR_SIMILARITY


async def near_filter(
    agent: Agent, *, transport: httpx.AsyncBaseTransport | None
) -> NearFilter | None:
    """이미 있는 문장과 근접한 새 문장을 거르는 거르개. 임베딩이 연결돼 있지 않으면 None."""
    connection = await connections.get_connection(agent.db, role=ConnectionRole.EMBEDDING)
    if connection is None:
        return None
    return NearFilter(connection=connection, transport=transport)


async def generate_label(
    agent: Agent,
    *,
    label_id: int,
    name: str,
    count: int,
    labels: list[Label],
    near: NearFilter | None,
) -> tuple[int, int]:
    """라벨 하나의 새 문장을 묶음마다 만들고 걸러 넣는다. (더한 수, 근접해 버린 수)."""
    db = agent.db
    await agent.say(f"{name} 새 문장 {count}개를 만든다.")
    samples = await helper_tools.label_samples(
        db, dataset_id=agent.dataset_id, label_id=label_id, limit=GENERATE_SAMPLE_COUNT
    )
    others = []
    for label in labels:
        if label.id != label_id:
            for text in await helper_tools.label_samples(
                db,
                dataset_id=agent.dataset_id,
                label_id=label.id,
                limit=GENERATE_OTHER_SAMPLE_COUNT,
            ):
                others.append(f"({label.name}) {text}")
    reference: np.ndarray | None = None
    if near is not None:
        try:
            reference = await near.label_units(db, dataset_id=agent.dataset_id, label_id=label_id)
        except ExternalServiceError as error:
            await agent.notice(f"근접 거르기 없음 · {error.message}", tone="warn")
            near = None
    questions = semantic_checks.jev_questions(labels)
    kept: list[str] = []
    # 더한 새 문장의 벡터 (뒤에 만든 문장이 앞서 더한 문장과 근접한지 본다)
    made: list[np.ndarray] = []
    # Jev가 다른 라벨로 읽어 버린 문장 '(Jev가 고른 라벨) 문장'
    dropped: list[str] = []
    rejected = 0
    near_rejected = 0
    jev_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    max_attempts = math.ceil(count / GENERATE_BATCH) * GENERATE_ROUNDS_FACTOR + 1
    while len(kept) < count and attempts < max_attempts:
        attempts += 1
        wanted = min(GENERATE_BATCH, count - len(kept))
        texts = await _ask_texts(
            agent,
            name=name,
            count=wanted,
            samples=samples,
            others=others,
            made=kept[-GENERATE_MADE_SHOWN:],
            dropped=dropped[-GENERATE_DROPPED_SHOWN:],
        )
        hashes = [make_text_hash(text) for text in texts]
        existing = await helper_tools.existing_hashes(
            db, dataset_id=agent.dataset_id, hashes=hashes
        )
        fresh: list[str] = []
        for text, text_hash in zip(texts, hashes, strict=True):
            if text_hash in existing or text_hash in seen:
                rejected += 1
                continue
            seen.add(text_hash)
            fresh.append(text)
        units: np.ndarray | None = None
        if near is not None and reference is not None and fresh:
            try:
                units = await near.units(fresh)
            except ExternalServiceError as error:
                await agent.notice(f"근접 거르기 없음 · {error.message}", tone="warn")
                near = None
        for index, text in enumerate(fresh):
            if len(kept) >= count:
                rejected += 1
                continue
            unit = units[index] if units is not None else None
            is_close = (
                unit is not None
                and reference is not None
                and _is_near(unit, reference=reference, made=made)
            )
            if is_close:
                rejected += 1
                near_rejected += 1
                continue
            if agent.jev_connection is not None:
                decision = await jev.decide(
                    agent.jev_connection,
                    state={"text": text},
                    questions=questions,
                    transport=agent.jev_transport,
                )
                await helper_service.add_usage(db, run_id=agent.run_id, jev_calls=1)
                answer = decision.answers.get(semantic_checks.JEV_QUESTION)
                prob = float(answer.probabilities.get(name, 0.0)) if answer else 0.0
                passed = answer is not None and answer.choice == name and prob >= GENERATE_MIN_JEV
                if len(jev_rows) < CARD_ROWS:
                    jev_rows.append(
                        {
                            "text": text,
                            "from": "새 문장",
                            "choice": answer.choice if answer else "—",
                            "prob": round(prob, 3),
                            "decision": "apply" if passed else "drop",
                        }
                    )
                if not passed:
                    rejected += 1
                    dropped.append(f"({answer.choice if answer else '—'}) {text}")
                    continue
            kept.append(text)
            if unit is not None:
                made.append(unit)
    if jev_rows:
        await agent.emit(
            HelperEventKind.JEV,
            {
                "meta": f"통과 {len(kept)} · 버림 {rejected} · 기준 {GENERATE_MIN_JEV}",
                "rows": jev_rows,
                "more": "",
            },
        )
    records = [
        Record(
            dataset_id=agent.dataset_id,
            label_id=label_id,
            text=text,
            text_hash=make_text_hash(text),
            extra=dict(classification_service.NEW_SENTENCE_EXTRA),
        )
        for text in kept
    ]
    db.add_all(records)
    await db.flush()
    added = await helper_tools.log_created(
        db,
        run_id=agent.run_id,
        step=agent.step_no,
        dataset_id=agent.dataset_id,
        records=records,
        tool="LLM → 근접 거르기 → Jev" if near is not None else "LLM → Jev",
    )
    return added, near_rejected


async def _ask_texts(
    agent: Agent,
    *,
    name: str,
    count: int,
    samples: list[str],
    others: list[str],
    made: list[str],
    dropped: list[str],
) -> list[str]:
    """LLM에 새 문장을 JSON으로 받는다. 모양이 틀리거나 너무 짧은 · 긴 문장은 버린다. LLM 카드를 적는다."""
    system = (
        "너는 분류 학습 데이터의 새 문장을 만든다. 보기 문장과 모양 · 길이 · 말투를 맞추고, "
        "보기와 같거나 거의 같은 문장은 쓰지 않는다. 다른 라벨로 읽힐 문장은 쓰지 않는다. "
        "문장끼리도 대상과 사실이 겹치지 않게 서로 다른 것을 쓰고, 이미 만든 문장의 대상은 다시 쓰지 않는다. "
        '답은 JSON 하나만: {"texts": ["…"]}'
    )
    user = (
        f"라벨: {name}\n만들 수: {count}\n보기:\n"
        + "\n".join(f"- {text}" for text in samples)
        + "\n다른 라벨 보기(이렇게 읽히면 안 된다):\n"
        + "\n".join(f"- {text}" for text in others)
    )
    if made:
        user += "\n이미 만든 문장(같은 대상 · 같은 사실은 다시 쓰지 않는다):\n" + "\n".join(
            f"- {text}" for text in made
        )
    if dropped:
        user += "\n다른 라벨로 읽혀 버린 문장(이런 주제 · 표현은 피한다):\n" + "\n".join(
            f"- {text}" for text in dropped
        )
    reply = await agent.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ],
        [],
        max_tokens=GENERATE_MAX_TOKENS,
    )
    used = reply.input_tokens + reply.output_tokens
    texts = _parse_texts(reply.content)
    await agent.emit(
        HelperEventKind.LLM,
        {
            "meta": f"새 문장 {len(texts)} · {used:,}토큰",
            "rows": [
                {"text": text, "from": "", "to": name, "why": "새 문장"}
                for text in texts[:CARD_ROWS]
            ],
        },
    )
    return texts


def _parse_texts(content: str) -> list[str]:
    """LLM 답에서 {"texts": [...]}를 읽는다. 앞뒤 글이나 코드 울타리가 있어도 첫 { … 마지막 }을 읽는다."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(content[start : end + 1])
    except ValueError:
        return []
    texts = parsed.get("texts") if isinstance(parsed, dict) else None
    if not isinstance(texts, list):
        return []
    cleaned = [str(text).strip() for text in texts]
    return [
        text
        for text in cleaned
        if records_service.SHORT_TEXT_LENGTH <= len(text) <= GENERATED_TEXT_MAX_LENGTH
    ]


def parse_json_items(content: Any) -> list[dict[str, Any]]:
    """LLM 답 {"items": [...]}의 줄들. 모양이 틀리면 빈 목록."""
    items = content.get("items") if isinstance(content, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def batched(items: list[Any], size: int = JUDGE_BATCH) -> list[tuple[Any, ...]]:
    return list(itertools.batched(items, size))
