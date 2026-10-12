"""생성 모듈 테이블 (generation_): LLM 학습 데이터의 지시 · 응답, 검토 결정 기록.

대화 하나는 '마지막 응답 앞까지 = 지시'와 '마지막 응답 = 응답'으로 나눠 담는다. 앞 턴의 응답은 지시의 맥락이다.
나중에 한 지시에 응답을 여럿 두어(다시 만든 응답 · 비교용 응답) 선호 데이터를 만들 수 있게 하려는 것이다.
- 지시: 맥락 메시지들(system · user · assistant …, 마지막은 user), 같은 지시를 찾는 해시, 출처,
  글을 넣거나 바꿀 때 한 번 잰 문제 표시(marks), 사람이 '그대로 두기'로 둔 검사(kept), 휴지통.
  문제 표시는 대화 전체(맥락 + 응답)를 잰 것이라 대화 단위로 지시 줄에 둔다(진단이 요청마다 글을 읽지 않게).
- 응답: 지시에 대한 마지막 응답 글. 생각 과정(reasoning)은 따로 둔다(다음 판).
- 결정 기록: 검토에서 한 결정(빼기 · 그대로 두기 · 가리기 · 자르기 · 다시 쓰기)의 전 · 후. 되돌리기에 쓴다.
- 뜻 분석: 지시 · 응답을 임베딩해 만든 한 번의 분석(지도 점 · 근접 중복 쌍 · Jev 판정). 데이터셋마다 다 만든 것은 하나다.
train · valid · test는 나누지 않는다(한 덩어리).

데이터셋(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이고, 생성 테이블이 참조한다.
생성 테이블은 시스템 테이블만 참조하고, 다른 모듈의 테이블은 모른다.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dent.system.db import Base, check_in
from dent.system.models import EMPTY_JSON_OBJECT
from dent.system.text import TEXT_HASH_LENGTH

# 대화 하나의 최대 글자 수(모든 메시지를 합친 것). 이보다 긴 대화는 가져오지 않는다.
CONVERSATION_MAX_LENGTH = 400_000

# 대화 하나의 최대 메시지 수
CONVERSATION_MAX_MESSAGES = 200

# 출처 · 결정 같은 고정 값의 최대 길이
ENUM_MAX_LENGTH = 16

# 검사 이름의 최대 길이 (rules.CheckKey)
CHECK_KEY_MAX_LENGTH = 32

# 빈 JSON 목록 (사람이 '그대로 두기'로 둔 검사가 없음)
EMPTY_JSON_LIST = sql_text("'[]'::jsonb")


class Role(StrEnum):
    """메시지의 역할. 원본의 다른 이름(human · gpt …)은 가져올 때 이것으로 맞춘다."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Shape(StrEnum):
    """들어온 원본의 모양. 가져오기가 이 모양대로 칸을 읽어 대화로 맞춘다."""

    # messages[{role, content}] (TRL · OpenAI · Tülu)
    MESSAGES = "messages"
    # conversations[{from, value}] (ShareGPT · LLaMA-Factory)
    SHAREGPT = "sharegpt"
    # instruction · input · output (+ system) (Alpaca · KoAlpaca)
    ALPACA = "alpaca"
    # input1 · output1 · input2 · output2 … (턴마다 칸)
    TURNS = "turns"


class Source(StrEnum):
    """지시 · 응답이 어디서 왔는지. 내보내는 파일의 출처 칸이 된다."""

    # 가져온 원본
    ORIGINAL = "original"
    # LLM이 만든 것 (다음 판: 응답 만들기 · 다시 쓰기)
    SYNTHETIC = "synthetic"
    # 사람이 쓰거나 고친 것
    HUMAN = "human"


class Action(StrEnum):
    """검토에서 한 결정."""

    # 대화를 휴지통으로 (학습에서 빠진다)
    DROP = "drop"
    # 문제가 아니다 · 그대로 쓴다 (그 검사에서 다시 묻지 않는다)
    KEEP = "keep"
    # 개인정보를 [이메일]처럼 가린다
    MASK = "mask"
    # 반복 루프를 루프 앞에서 자른다
    CUT = "cut"
    # LLM이 응답을 다시 쓴다 (출처는 synthetic)
    REWRITE = "rewrite"


class AnalysisStatus(StrEnum):
    """뜻 분석 한 번의 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# 만드는 중으로 보는 뜻 분석 상태. 데이터셋마다 한 번에 하나만 돈다.
ANALYSIS_BUILDING_STATUSES = (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING)


class Prompt(Base):
    """지시 한 건: 마지막 응답 앞까지의 대화(맥락), 문제 표시, 출처."""

    __tablename__ = "generation_prompts"
    __table_args__ = (
        CheckConstraint(check_in("source", Source), name="source"),
        # 같은 지시 찾기(중복)는 데이터셋 안에서 해시로 묶는다.
        Index("ix_generation_prompts_same_text", "dataset_id", "text_hash"),
        # 가져오기를 다시 돌릴 때 그 가져오기가 넣은 줄을 지운다.
        Index("ix_generation_prompts_dataset_id_import_id", "dataset_id", "import_id"),
    )

    # 지시 번호 (화면에서는 대화 번호)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 지시도 함께 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 이 지시를 넣은 가져오기 번호. 만들었거나 기록이 지워졌으면 비어 있다.
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_imports.id", ondelete="SET NULL")
    )

    # 맥락 메시지들 [{"role": "system" · "user" · "assistant", "content": 글}]. 마지막은 user다.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    # 같은 지시를 찾는 열쇠 (정규화한 맥락 전체의 sha256)
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 출처: original · synthetic · human
    source: Mapped[str] = mapped_column(
        String(ENUM_MAX_LENGTH), server_default=Source.ORIGINAL.value
    )

    # 글을 넣거나 바꿀 때 한 번 잰 문제 표시 {검사: [{"turn": 메시지 순서, "snippet": 조각}]} (rules.measure)
    marks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 사람이 '그대로 두기'로 둔 검사들 ["short", …]. 이 검사로는 다시 세지 않는다.
    kept: Mapped[list[str]] = mapped_column(JSONB, server_default=EMPTY_JSON_LIST)

    # 남은 검사들 = marks의 검사 가운데 kept에 없는 것. marks · kept를 바꿀 때 함께 고친다(진단이 SQL로 바로 세게).
    open_checks: Mapped[list[str]] = mapped_column(JSONB, server_default=EMPTY_JSON_LIST)

    # 휴지통에 넣은 시각
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 고칠 때마다 1씩 오르는 번호 (다른 창에서 먼저 고쳤는지 알아본다)
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")

    # 원본에서 쓰지 않은 남는 필드들 {열 이름: 값}
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 만든 시각 · 고친 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Response(Base):
    """응답 한 건: 지시에 대한 마지막 응답 글과 출처."""

    __tablename__ = "generation_responses"
    __table_args__ = (CheckConstraint(check_in("source", Source), name="source"),)

    # 응답 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호 (세기를 빨리 하려고 적어 둔다). 데이터셋을 지우면 응답도 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 지시 번호. 지시를 지우면 응답도 지운다. 지시마다 응답을 찾는다.
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), index=True
    )

    # 응답 글
    content: Mapped[str] = mapped_column(Text)

    # 생각 과정 (추론 SFT, 다음 판). 없으면 비어 있다.
    reasoning: Mapped[str | None] = mapped_column(Text)

    # 출처: original · synthetic · human
    source: Mapped[str] = mapped_column(
        String(ENUM_MAX_LENGTH), server_default=Source.ORIGINAL.value
    )

    # 만든 시각 · 고친 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Change(Base):
    """결정 한 건의 전 · 후 (사람의 검토 결정 · 도우미가 바꾼 것). 되돌리기는 지금 값이 '후'와 같을 때만 '전'으로 돌린다.

    도우미가 바꿨으면 실행 번호와 카드(사건) 번호를 적는다. 도우미 창의 되돌리기가 카드 · 실행 단위로 찾는다.
    """

    __tablename__ = "generation_changes"
    __table_args__ = (
        CheckConstraint(check_in("action", Action), name="action"),
        # 결정 기록은 데이터셋마다 최근 것부터 본다.
        Index("ix_generation_changes_dataset_id_id", "dataset_id", "id"),
    )

    # 결정 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 기록도 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 대화(지시) 번호. 대화를 영구히 지우면 기록도 지운다. 대화마다 기록을 찾는다.
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), index=True
    )

    # 어느 검사를 보고 한 결정인지 (rules.CheckKey). 검사 없이 한 결정이면 비어 있다.
    check_key: Mapped[str | None] = mapped_column(String(CHECK_KEY_MAX_LENGTH))

    # 결정: drop · keep · mask · cut · rewrite
    action: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 바꾸기 전 · 후 상태 {"trashed": …, "kept": […], "messages": […], "content": …, "source": …} (바뀐 칸만)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)

    # 도우미 실행 번호 (사람이 한 결정이면 비어 있다). 실행마다 바꾼 것을 찾는다.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_helper_runs.id", ondelete="SET NULL"), index=True
    )

    # 도우미가 남긴 바꾼 카드(사건) 번호. 카드 하나를 되돌릴 때 찾는다.
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_helper_events.id", ondelete="SET NULL")
    )

    # 결정한 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 되돌린 시각. 비어 있으면 아직 되돌리지 않았다.
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Analysis(Base):
    """뜻 분석 한 번: 지시 · 응답을 임베딩해 지도 · 근접 중복 · 과제 무리 · Jev 판정을 만든다.

    데이터셋마다 다 만든 분석은 가장 최근 것 하나만 남긴다. 만드는 중인 것도 하나뿐이다(부분 UNIQUE).
    """

    __tablename__ = "generation_analyses"
    __table_args__ = (
        CheckConstraint(check_in("status", AnalysisStatus), name="status"),
        Index(
            "uq_generation_analyses_building",
            "dataset_id",
            unique=True,
            postgresql_where=sql_text("status IN ('queued', 'running')"),
        ),
    )

    # 분석 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋별로 분석을 찾는다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 쓴 임베딩 모델 번호 (작업이 시작할 때 정한다)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_embedding_models.id", ondelete="SET NULL")
    )

    # 상태: queued · running · done · failed · canceled
    status: Mapped[str] = mapped_column(
        String(ENUM_MAX_LENGTH), server_default=AnalysisStatus.QUEUED.value
    )

    # 좌표를 만든 방법과 값
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 분석 결과 요약: 과제 무리 · 근접 중복 · Jev 판정 상태 (semantic.py가 만든다)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 분석한 대화 수 (휴지통 밖)
    conversation_count: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 만들기 시작할 때 데이터셋의 수정 시각. 지금이 더 늦으면 분석 이후 바뀐 것이다.
    dataset_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 이 분석을 만드는 작업 번호
    job_id: Mapped[int | None] = mapped_column(ForeignKey("system_jobs.id", ondelete="SET NULL"))

    # 실패 이유 (한국어 문장)
    error: Mapped[str | None] = mapped_column(Text)

    # 만들기를 누른 시각 · 끝난 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MapPoint(Base):
    """지도의 점 하나: 지시 한 건의 2차원 좌표와 과제 무리."""

    __tablename__ = "generation_map_points"

    # 점 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 분석 번호. 분석을 지우면 점도 지운다. 분석별로 점을 읽는다.
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("generation_analyses.id", ondelete="CASCADE"), index=True
    )

    # 대화(지시) 번호. 대화를 지우면 점도 지운다(DB가 이 칸으로 찾는다).
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), index=True
    )

    # 좌표 [-1, 1]
    x: Mapped[float] = mapped_column(REAL)
    y: Mapped[float] = mapped_column(REAL)

    # 과제 무리 번호 (지시 벡터를 KMeans로 나눈 것)
    cluster: Mapped[int] = mapped_column(SmallInteger, server_default="0")


class NearDuplicate(Base):
    """근접 중복 한 쌍: 글자는 달라도 뜻이 거의 같은 두 지시. 작은 번호 · 큰 번호 순서로 한 번만 적는다."""

    __tablename__ = "generation_near_duplicates"
    __table_args__ = (
        # 대화를 지울 때 DB가 두 칸으로 쌍을 찾고, 대화마다 가까운 짝을 찾는다.
        Index("ix_generation_near_duplicates_prompt_a", "prompt_a"),
        Index("ix_generation_near_duplicates_prompt_b", "prompt_b"),
    )

    # 분석 번호
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("generation_analyses.id", ondelete="CASCADE"), primary_key=True
    )

    # 앞 대화 · 뒤 대화 (작은 번호가 앞). 대화를 지우면 쌍도 지운다.
    prompt_a: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), primary_key=True
    )
    prompt_b: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), primary_key=True
    )

    # 지시 벡터의 코사인 유사도
    similarity: Mapped[float] = mapped_column(REAL)


class Judgment(Base):
    """Jev 판정 한 건: 뜻 분석이 한 대화를 Jev에 물은 결과. 글이 바뀌면 지운다(옛 글의 판정이라서)."""

    __tablename__ = "generation_judgments"

    # 분석 번호. 분석을 지우면 판정도 지운다.
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("generation_analyses.id", ondelete="CASCADE"), primary_key=True
    )

    # 대화(지시) 번호. 대화마다 판정을 찾는다.
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("generation_prompts.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    # 지시 벡터와 응답 벡터의 코사인 유사도 (지시 불일치 후보를 고른 값)
    similarity: Mapped[float | None] = mapped_column(REAL)

    # '응답이 지시를 따른다'의 예 확률. 묻지 못했으면 비운다.
    follows: Mapped[float | None] = mapped_column(REAL)

    # '거절이 마땅하다(해로운 · 할 수 없는 요청)'의 예 확률. 거절로 표시된 대화만 묻는다.
    refusal_ok: Mapped[float | None] = mapped_column(REAL)
