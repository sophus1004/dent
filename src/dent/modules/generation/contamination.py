"""평가셋 오염 대조: '평가셋'으로 표시한 생성 데이터셋의 지시와 학습 데이터셋의 지시를 견준다.

평가셋은 사용자가 가져와 데이터셋 설정에서 표시한다(대부분 다시 나눠 줄 수 없어 DENT에 넣지 않는다).
작업 ("generation", "contamination")이 데이터셋 하나를 대조해 겹친 지시를 generation_contaminations에 적고,
그때의 평가셋 표시(번호 · 수정 시각)를 설정에 남긴다. 평가셋이 바뀌면(표시 · 데이터 추가 · 고치기) 표시가 달라져
진단이 '대조 전'으로 기다리고, 표시를 바꾸거나 가져오기를 마칠 때 대조 작업을 다시 넣는다.
글은 지시(사용자 말을 이은 것, system 뺌)끼리 견준다. 계산은 system/contamination.py다.

1. 8-gram: 평가 문항의 낱말 절반이 넘게 같은 8-gram에 들면 겹침.
2. 뜻(임베딩이 연결돼 있을 때): 8-gram에 걸리지 않은 지시마다 임베딩이 가까운 평가 문항을 후보로 두고,
   LLM이 연결돼 있으면 같은 문제(말만 바꿈 · 다른 언어로 옮김)인지 판정해 '예'만 겹침으로 본다.
   LLM이 없거나 실패하면 코사인이 아주 높은(거의 같은 글) 후보만 겹침으로 본다. 임베딩은 뜻 분석과 같은 캐시를 쓴다.
쓴 방법은 설정 contamination_by에 남겨 진단이 보인다(임베딩이 없으면 8-gram만).
"""

import asyncio
import itertools
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.generation import problems, semantic
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import (
    Contamination,
    ContaminationBy,
    ContaminationMethod,
    DatasetSettings,
    Prompt,
)
from dent.system import connections, contamination, embedding, jobs, llm
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, ConnectionRole, Dataset, EmbeddingModel, Job
from dent.system.text import make_text_hash, normalize_text

logger = logging.getLogger("dent.generation.contamination")

CONTAMINATION_JOB_KIND = "contamination"

# 대조할 때 한 번에 읽는 지시 수
BATCH_PROMPTS = 2_000

# 대조 상태: 평가셋 없음 · 대조 전(평가셋이 바뀜) · 대조함
STATE_NO_EVAL = "no_eval"
STATE_STALE = "stale"
STATE_DONE = "done"

# 작업 단계: 8-gram 대조 · 임베딩(캐시에 없는 지시만) · LLM 판정
PHASE_NGRAM = "ngram"
PHASE_EMBEDDING = "embedding"
PHASE_JUDGING = "judging"

# LLM에 한 번에 묻는 후보 쌍 수와 함께 보내는 요청 수
JUDGE_BATCH = 10
JUDGE_CONCURRENCY = 4

# 대조 한 번에 LLM에 묻는 후보 쌍의 상한(가까운 쌍부터). 넘는 쌍은 임베딩 유사도로만 본다.
# 쌍마다 약 150토큰이라 상한까지 물어도 약 30만 토큰이다.
MAX_JUDGE_PAIRS = 2_000

# LLM 답의 최대 토큰 (쌍마다 한 줄 까닭 + 판정)
JUDGE_MAX_TOKENS = 1_500

# 판정에 보이는 글의 최대 글자 수 (긴 지시는 앞부분만)
JUDGE_TEXT_CHARS = 1_200

# 까닭을 먼저 쓰게 한다(판정을 먼저 쓰면 까닭을 판정에 맞춰 지어낸다).
JUDGE_SYSTEM = """학습 데이터의 지시가 평가셋 문항을 베끼거나, 말을 바꾸거나, 다른 언어로 옮긴 것인지 가린다.
같은 문제(같은 조건 · 수 · 묻는 것)를 말만 바꾸거나 옮긴 것이면 yes, 주제만 같거나 수 · 조건 · 묻는 것이 다르면 no,
글만으로 가를 수 없으면 unsure.
답은 JSON 목록만: [{"ref": "r1", "why": "한 줄", "same": "yes|no|unsure"}] (why를 먼저 쓰고 그에 맞게 same을 고른다)"""

# LLM 답에서 겹침으로 보는 값
SAME_YES = "yes"


async def eval_dataset_ids(db: AsyncSession) -> list[int]:
    """'평가셋'으로 표시한 생성 데이터셋 번호들."""
    return list(
        (
            await db.scalars(
                select(DatasetSettings.dataset_id)
                .where(DatasetSettings.is_eval)
                .order_by(DatasetSettings.dataset_id)
            )
        ).all()
    )


async def eval_signature(db: AsyncSession) -> str | None:
    """평가셋들의 표시: 번호와 수정 시각을 이은 글. 평가셋이 없으면 None."""
    rows = (
        await db.execute(
            select(Dataset.id, Dataset.updated_at)
            .join(DatasetSettings, DatasetSettings.dataset_id == Dataset.id)
            .where(DatasetSettings.is_eval, Dataset.module == generation_service.MODULE_NAME)
            .order_by(Dataset.id)
        )
    ).all()
    if not rows:
        return None
    return ";".join(f"{dataset_id}:{updated_at.isoformat()}" for dataset_id, updated_at in rows)


async def state(db: AsyncSession, *, dataset_id: int) -> str:
    """이 데이터셋의 대조 상태: 평가셋 없음 · 대조 전 · 대조함."""
    signature = await eval_signature(db)
    if signature is None:
        return STATE_NO_EVAL
    found = await db.get(DatasetSettings, dataset_id)
    if found is None or found.contamination_signature != signature:
        return STATE_STALE
    return STATE_DONE


async def overlap(db: AsyncSession, *, dataset_id: int) -> tuple[int, int]:
    """겹친 문항의 몫이 가장 큰 평가셋의 (휴지통 밖 문항 수, 이 데이터셋의 남은 오염 지시와 겹친 문항 수).

    평가셋이 없으면 (0, 0). 사람이 '그대로 두기'로 둔 지시 · 휴지통의 지시는 세지 않는다.
    """
    eval_ids = [dataset for dataset in await eval_dataset_ids(db) if dataset != dataset_id]
    sizes = dict(
        (
            await db.execute(
                select(Prompt.dataset_id, func.count())
                .where(Prompt.dataset_id.in_(eval_ids), generation_service.IS_ACTIVE)
                .group_by(Prompt.dataset_id)
            )
        ).all()
    )
    eval_prompt = aliased(Prompt)
    overlapped = dict(
        (
            await db.execute(
                select(eval_prompt.dataset_id, func.count(func.distinct(eval_prompt.id)))
                .join(Contamination, Contamination.eval_prompt_id == eval_prompt.id)
                .where(
                    Contamination.prompt_id.in_(
                        problems.contamination_ids(dataset_id).scalar_subquery()
                    ),
                    eval_prompt.trashed_at.is_(None),
                )
                .group_by(eval_prompt.dataset_id)
            )
        ).all()
    )
    best = (0, 0)
    for eval_id, size in sizes.items():
        found = int(overlapped.get(eval_id, 0))
        if size and (not best[0] or found / size > best[1] / best[0]):
            best = (int(size), found)
    return best


async def queue_check(db: AsyncSession, *, dataset_id: int) -> Job | None:
    """이 데이터셋의 대조 작업을 넣는다(평가셋이 없거나 자기가 평가셋이면 넣지 않음). 커밋은 부른 쪽이 한다.

    이미 기다리는 대조 작업이 있으면 새로 넣지 않고 그 작업을 돌려준다(작업 실행기 여럿이 평가셋 가져오기를
    함께 마쳐 동시에 넣어도 하나만 남는다).
    """
    found = await generation_service.find_settings(db, dataset_id=dataset_id)
    if found.is_eval or await eval_signature(db) is None:
        return None
    return await jobs.enqueue_job(
        db,
        module=generation_service.MODULE_NAME,
        kind=CONTAMINATION_JOB_KIND,
        params={"dataset_id": dataset_id},
        dataset_id=dataset_id,
        dedupe_key=jobs.dedupe_key_of(
            generation_service.MODULE_NAME, CONTAMINATION_JOB_KIND, dataset_id
        ),
    )


async def queue_all(db: AsyncSession) -> int:
    """평가셋이 아닌 모든 생성 데이터셋의 대조 작업을 넣는다(평가셋이 바뀐 때). 넣은 수. 커밋은 부른 쪽이 한다."""
    dataset_ids = (
        await db.scalars(
            select(Dataset.id)
            .where(Dataset.module == generation_service.MODULE_NAME)
            .order_by(Dataset.id)
        )
    ).all()
    queued = 0
    for dataset_id in dataset_ids:
        queued += await queue_check(db, dataset_id=dataset_id) is not None
    return queued


async def run_check(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
    llm_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """데이터셋 하나를 평가셋들과 대조해 겹친 지시를 적는다(앞서 적은 것은 지운다). 결과 요약.

    임베딩 · LLM이 안 되면 작업을 실패시키지 않고 할 수 있는 방법까지만 한다(쓴 방법을 설정에 남긴다).
    대조는 다른 데이터셋(평가셋)을 읽는 작업이라, 그사이 평가셋의 문항이 지워지면(평가셋을 지움 · 가져오기 실패 ·
    휴지통 비우기, 다른 작업 실행기가 함께 돌 수 있다) 겹친 줄을 적지 못한다. 그때는 대조를 다시 넣고 끝낸다.
    transport · llm_transport는 테스트가 가짜 임베딩 · LLM을 줄 때만.
    """
    try:
        return await _run_check(
            db,
            dataset_id=dataset_id,
            job_id=job_id,
            transport=transport,
            llm_transport=llm_transport,
        )
    except IntegrityError:
        await db.rollback()
        logger.info("데이터셋 %d번: 대조하는 동안 평가셋이 바뀌어 다시 대조합니다.", dataset_id)
        again = await queue_check(db, dataset_id=dataset_id)
        await db.commit()
        return {"restarted": again is not None}


async def _run_check(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None,
    llm_transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    """run_check의 본문: 8-gram → 뜻으로 대조하고 적는다."""
    signature = await eval_signature(db)
    eval_ids = [dataset for dataset in await eval_dataset_ids(db) if dataset != dataset_id]
    eval_items = [
        (prompt_id, semantic.instruction_text(messages))
        for prompt_id, messages in (
            await db.execute(
                select(Prompt.id, Prompt.messages)
                .where(Prompt.dataset_id.in_(eval_ids), generation_service.IS_ACTIVE)
                .order_by(Prompt.id)
            )
        ).all()
    ]
    index = contamination.EvalIndex(eval_items)
    await db.execute(delete(Contamination).where(Contamination.dataset_id == dataset_id))
    total = await db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.dataset_id == dataset_id)
    )
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_NGRAM, total=total)
    # 8-gram에 걸리지 않은 휴지통 밖 지시 (번호, 지시 글 해시): 뜻으로 견줄 것
    unmatched: list[tuple[int, str]] = []
    done = ngram_count = 0
    last_id = 0
    while True:
        rows = (
            await db.execute(
                select(Prompt.id, Prompt.messages, generation_service.IS_ACTIVE)
                .where(Prompt.dataset_id == dataset_id, Prompt.id > last_id)
                .order_by(Prompt.id)
                .limit(BATCH_PROMPTS)
            )
        ).all()
        if not rows:
            break
        matches = []
        for prompt_id, messages, is_active in rows:
            text = semantic.instruction_text(messages)
            match = index.best_match(text) if len(index) else None
            if match is not None:
                matches.append(
                    {
                        "prompt_id": prompt_id,
                        "dataset_id": dataset_id,
                        "eval_prompt_id": match.eval_id,
                        "method": ContaminationMethod.NGRAM.value,
                        "coverage": match.coverage,
                    }
                )
            elif is_active and _is_comparable(text):
                unmatched.append((prompt_id, make_text_hash(text)))
        if matches:
            await db.execute(insert(Contamination), matches)
        ngram_count += len(matches)
        last_id = rows[-1][0]
        done += len(rows)
        await jobs.set_progress(db, job_id=job_id, done=done, total=total)
        await db.commit()

    by, found, facts = await _match_by_meaning(
        db,
        job_id=job_id,
        eval_items=[(eval_id, text) for eval_id, text in eval_items if _is_comparable(text)],
        unmatched=unmatched,
        transport=transport,
        llm_transport=llm_transport,
    )
    if found:
        await db.execute(
            insert(Contamination), [{**row, "dataset_id": dataset_id} for row in found]
        )
    stored = await db.get(DatasetSettings, dataset_id)
    if stored is None:
        stored = await generation_service.find_settings(db, dataset_id=dataset_id)
        db.add(stored)
    stored.contamination_signature = signature
    stored.contamination_checked_at = datetime.now(UTC)
    stored.contamination_by = by.value
    await db.commit()
    by_method = {
        method.value: sum(row["method"] == method.value for row in found)
        for method in (ContaminationMethod.PARAPHRASE, ContaminationMethod.SIMILAR)
    }
    return {
        "conversations": done,
        "contaminated": ngram_count + len(found),
        "ngram": ngram_count,
        **by_method,
        "by": by.value,
        "eval_items": len(eval_items),
        **facts,
    }


def _is_comparable(text: str) -> bool:
    """뜻으로 견줄 만한 글인지: 낱말이 짧은 문항 기준 이상 (짧은 인사 같은 글이 아무 문항에나 걸리지 않게)."""
    return len(contamination.words_of(text)) >= contamination.MIN_SHORT_WORDS


async def _match_by_meaning(
    db: AsyncSession,
    *,
    job_id: int,
    eval_items: list[tuple[int, str]],
    unmatched: list[tuple[int, str]],
    transport: httpx.AsyncBaseTransport | None,
    llm_transport: httpx.AsyncBaseTransport | None,
) -> tuple[ContaminationBy, list[dict[str, Any]], dict[str, Any]]:
    """8-gram에 걸리지 않은 지시를 뜻으로 견준다. (쓴 방법, 넣을 오염 줄들(데이터셋 번호 뺌), 결과 요약)."""
    embedding_connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if embedding_connection is None:
        return ContaminationBy.NGRAM, [], {}
    llm_connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    by = ContaminationBy.LLM if llm_connection is not None else ContaminationBy.EMBEDDING
    if not eval_items or not unmatched:
        return by, [], {}
    try:
        model = await embedding.ensure_model(db, embedding_connection, transport=transport)
        eval_vectors, train_vectors = await _vectors(
            db,
            job_id=job_id,
            connection=embedding_connection,
            model=model,
            eval_items=eval_items,
            unmatched=unmatched,
            transport=transport,
        )
    except ExternalServiceError as error:
        return ContaminationBy.NGRAM, [], {"embedding_error": error.message}
    candidates = await asyncio.to_thread(
        contamination.semantic_candidates, train_vectors, eval_vectors
    )
    del eval_vectors, train_vectors
    facts: dict[str, Any] = {"candidates": len(candidates)}
    decided: dict[int, tuple[contamination.Candidate, ContaminationMethod]] = {}
    rest = candidates
    if llm_connection is not None and candidates:
        judged = candidates[:MAX_JUDGE_PAIRS]
        answers, tokens, error = await _judge(
            db,
            job_id=job_id,
            connection=llm_connection,
            pairs=judged,
            eval_items=eval_items,
            unmatched=unmatched,
            transport=llm_transport,
        )
        facts.update(judged=len(answers), llm_tokens=tokens)
        if error is not None:
            by = ContaminationBy.EMBEDDING
            facts["llm_error"] = error
        for candidate, answer in zip(judged, answers, strict=False):
            if answer == SAME_YES and candidate.train not in decided:
                decided[candidate.train] = (candidate, ContaminationMethod.PARAPHRASE)
        rest = candidates[len(answers) :]
    # LLM이 보지 않은 후보: 거의 같은 글만 겹침으로 본다.
    for candidate in rest:
        is_near_copy = candidate.similarity >= contamination.SIMILAR_LIMIT
        if is_near_copy and candidate.train not in decided:
            decided[candidate.train] = (candidate, ContaminationMethod.SIMILAR)
    found = [
        {
            "prompt_id": unmatched[train][0],
            "eval_prompt_id": eval_items[candidate.eval][0],
            "method": method.value,
            "similarity": candidate.similarity,
        }
        for train, (candidate, method) in sorted(decided.items())
    ]
    return by, found, facts


async def _vectors(
    db: AsyncSession,
    *,
    job_id: int,
    connection: Connection,
    model: EmbeddingModel,
    eval_items: list[tuple[int, str]],
    unmatched: list[tuple[int, str]],
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[np.ndarray, np.ndarray]:
    """평가 문항 · 견줄 지시의 벡터 (캐시에 없는 것만 임베딩해 저장). 받지 못하면 ExternalServiceError."""
    eval_texts = {make_text_hash(text): text for _, text in eval_items}
    eval_hashes = [make_text_hash(text) for _, text in eval_items]
    train_hashes = [text_hash for _, text_hash in unmatched]
    owners = {text_hash: prompt_id for prompt_id, text_hash in reversed(unmatched)}
    unique = sorted({*eval_hashes, *train_hashes})
    missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=unique)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_EMBEDDING, total=len(missing))
    await db.commit()
    embedded = 0
    for chunk in itertools.batched(missing, semantic.EMBED_CHUNK):
        texts = await _texts(db, text_hashes=chunk, known=eval_texts, owners=owners)
        vectors = await embedding.embed_texts(
            connection, [normalize_text(text) for text in texts], transport=transport
        )
        await embedding.save_vectors(db, model=model, text_hashes=chunk, vectors=vectors)
        embedded += len(chunk)
        await jobs.set_progress(db, job_id=job_id, done=embedded, total=len(missing))
        await db.commit()
    matrix = await embedding.load_vectors(db, model=model, text_hashes=unique)
    position = {text_hash: index for index, text_hash in enumerate(unique)}
    return (
        matrix[[position[h] for h in eval_hashes]],
        matrix[[position[h] for h in train_hashes]],
    )


async def _texts(
    db: AsyncSession, *, text_hashes: tuple[str, ...], known: dict[str, str], owners: dict[str, int]
) -> list[str]:
    """해시마다 지시 글: 평가 문항은 가진 글, 학습 지시는 그 해시를 가진 지시를 다시 읽는다."""
    wanted = [owners[h] for h in text_hashes if h not in known]
    read = {
        make_text_hash(text): text
        for text in (
            semantic.instruction_text(messages)
            for messages in (
                await db.scalars(select(Prompt.messages).where(Prompt.id.in_(wanted)))
            ).all()
        )
    }
    return [known.get(text_hash) or read[text_hash] for text_hash in text_hashes]


async def _judge(
    db: AsyncSession,
    *,
    job_id: int,
    connection: Connection,
    pairs: list[contamination.Candidate],
    eval_items: list[tuple[int, str]],
    unmatched: list[tuple[int, str]],
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[list[str], int, str | None]:
    """후보 쌍마다 LLM의 판정(yes · no · unsure, 앞에서부터 받은 만큼), 쓴 토큰, 실패했으면 그 까닭.

    JUDGE_BATCH쌍씩 묶어 JUDGE_CONCURRENCY건씩 함께 보낸다. 실패하면 그때까지 받은 판정만 돌려준다.
    """
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_JUDGING, total=len(pairs))
    await db.commit()
    prompt_ids = sorted({unmatched[pair.train][0] for pair in pairs})
    train_texts = {
        prompt_id: semantic.instruction_text(messages)
        for prompt_id, messages in (
            await db.execute(select(Prompt.id, Prompt.messages).where(Prompt.id.in_(prompt_ids)))
        ).all()
    }
    eval_texts = dict(eval_items)
    batches = [
        [
            (eval_texts[eval_items[pair.eval][0]], train_texts[unmatched[pair.train][0]])
            for pair in batch
        ]
        for batch in itertools.batched(pairs, JUDGE_BATCH)
    ]
    answers: list[str] = []
    tokens = 0
    for group in itertools.batched(batches, JUDGE_CONCURRENCY):
        try:
            replies = await asyncio.gather(
                *(_ask(connection, batch, transport=transport) for batch in group)
            )
        except ExternalServiceError as error:
            return answers, tokens, error.message
        for batch, (verdicts, used) in zip(group, replies, strict=True):
            answers += [verdicts.get(f"r{number}", "unsure") for number in range(1, len(batch) + 1)]
            tokens += used
        await jobs.set_progress(db, job_id=job_id, done=len(answers), total=len(pairs))
        await db.commit()
    return answers, tokens, None


async def _ask(
    connection: Connection,
    batch: list[tuple[str, str]],
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[dict[str, str], int]:
    """후보 쌍 묶음 하나를 LLM에 묻는다. ({ref: same}, 쓴 토큰). 못 받으면 ExternalServiceError."""
    body = "\n\n".join(
        f"r{number}\n평가 문항: {eval_text[:JUDGE_TEXT_CHARS]}\n학습 지시: {train_text[:JUDGE_TEXT_CHARS]}"
        for number, (eval_text, train_text) in enumerate(batch, start=1)
    )
    reply = await llm.chat(
        connection,
        messages=[
            llm.ChatMessage(role="system", content=JUDGE_SYSTEM),
            llm.ChatMessage(role="user", content=body),
        ],
        tools=[],
        max_tokens=JUDGE_MAX_TOKENS,
        transport=transport,
    )
    return _read_same(reply.content), reply.input_tokens + reply.output_tokens


def _read_same(reply: str) -> dict[str, str]:
    """LLM 답(JSON 목록)을 {ref: same}으로. 읽을 수 없으면 빈 사전(모두 unsure)."""
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        rows = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return {
        row["ref"]: str(row.get("same", "")).strip().lower()
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("ref"), str)
    }
