"""검색 모듈 테이블 (retrieval_): 질의 · 문서 · 판정, 데이터셋 설정, 반복 구간, 뜻 분석(지도 · 순위 · 근접 중복 · 제안),
도우미의 변경 기록.

검색 학습 데이터는 흐름 3단계로 다룬다: 1 문서 → (질의 만들기) → 2 질의 → (오답 찾기) → 3 하드 네거티브.
어느 모양으로 들어와도(문서만 · 쌍 · MRC · 세 쌍 · 점수) 이 세 표에 담는다.
- 질의: 출처(원본 · 합성 · 사람)와, 합성이면 만든 문서를 적는다. train · valid · test는 나누지 않는다(한 덩어리).
- 문서: 코퍼스. 같은 학습 글은 하나로 합친다. 나누기로 생긴 청크는 원문을 가리키고, 원문은 replaced_at을 적어 가린다.
  학습 글 = 머리말(제목 › 머리말 칸 › 구획 경로) + 본문에서 '떼기'로 고른 반복 구간을 뺀 것. 본문(text)은 바꾸지 않는다.
- 반복 구간: 여러 문서에 되풀이되는 문장 · 메타 모양(이메일 · 저작권 표시 …). 사람이 떼기 · 남김을 고른다.
- 판정: (질의, 문서) 한 쌍에 한 줄. 등급 0~3(0 = 오답, 1 이상 = 정답), 출처(원본 · 찾기 · 합성 · 도우미 · 사람).
  판정이 없는 쌍은 '모름'이다(오답이 아니다).

데이터셋(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이고, 검색 테이블이 참조한다.
검색 테이블은 시스템 테이블만 참조하고, 다른 모듈의 테이블은 모른다.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dent.system.db import Base, check_in
from dent.system.models import EMPTY_JSON_OBJECT, MODEL_NAME_MAX_LENGTH
from dent.system.text import TEXT_HASH_LENGTH

# 질의 한 건의 최대 글자 수. 이보다 길면 질의가 아니라 문서를 붙여 넣은 것이다.
QUERY_MAX_LENGTH = 2_000

# 문서 한 건의 최대 글자 수. 이보다 긴 문서는 가져오지 않는다(나누기 전 원문도 이 안이어야 한다).
DOCUMENT_MAX_LENGTH = 200_000

# 문서 제목의 최대 글자 수
TITLE_MAX_LENGTH = 1_000

# 원래 문서 번호(원본의 id 칸)의 최대 길이
DOC_KEY_MAX_LENGTH = 200

# 출처 · 상태 같은 고정 값의 최대 길이
ENUM_MAX_LENGTH = 16

# 판정 등급의 범위: 0 = 오답, 1~3 = 정답(클수록 더 맞음)
GRADE_MIN = 0
GRADE_MAX = 3

# 정답으로 보는 가장 낮은 등급
POSITIVE_MIN_GRADE = 1

# 머리말 칸 값 · 묶음 칸 값의 최대 글자 수
HEADER_MAX_LENGTH = 1_000
GROUP_KEY_MAX_LENGTH = 200

# 데이터셋 설정의 기본값: 질의 · 문서 최대 토큰, 질의마다 오답 수(bge train_group_size 8 = 정답 1 + 오답 7),
# 오답 찾기 순위 범위(FlagEmbedding 기본 10~210에 맞춤), 정답 유사도 대비 거짓 오답 경계,
# 질의마다 모아 두는 오답 풀(학습기가 매 에폭 7개를 무작위로 고른다), 쉬운 쌍 상한(몫)
DEFAULT_QUERY_MAX_TOKENS = 64
DEFAULT_DOC_MAX_TOKENS = 512
DEFAULT_NEGATIVES = 7
DEFAULT_MINE_RANK_FROM = 10
DEFAULT_MINE_RANK_TO = 200
DEFAULT_MINE_MARGIN = 0.95
DEFAULT_NEGATIVE_POOL = 20
DEFAULT_EASY_PAIR_CAP = 0.3

# 도우미 실행 설정의 기본값: 청크 오버랩(토큰, 없음), 청크마다 질의 수(bge 합성 데이터에서 흔한 2),
# 질의 가운데 질문형의 몫(%, 나머지는 검색어형. 실제 검색 기록은 검색어형이 많아 섞는다)
DEFAULT_CHUNK_OVERLAP = 0
DEFAULT_QUERIES_PER_CHUNK = 2
DEFAULT_QUESTION_SHARE = 60


class Shape(StrEnum):
    """들어온 원본의 모양. 모양이 흐름의 입구(몇 단계로 들어오나)를 정한다."""

    # 문서만 (입구 1)
    DOCUMENTS = "documents"
    # 질의 · 정답 문서 (입구 2)
    PAIR = "pair"
    # 질문 · 지문 · 답 (입구 2)
    MRC = "mrc"
    # 질의 · 정답 · 오답 여럿 (입구 3)
    TRIPLET = "triplet"
    # 질의 · 문서 · 점수 (입구 3)
    SCORED = "scored"


# 모양마다 흐름의 입구 단계
ENTRY_STAGE: dict[Shape, int] = {
    Shape.DOCUMENTS: 1,
    Shape.PAIR: 2,
    Shape.MRC: 2,
    Shape.TRIPLET: 3,
    Shape.SCORED: 3,
}


class QuerySource(StrEnum):
    """질의가 어디서 왔는지."""

    # 가져온 원본
    ORIGINAL = "original"
    # LLM이 문서로 만든 합성 질의
    SYNTHETIC = "synthetic"
    # 사람이 쓰거나 고친 질의
    HUMAN = "human"


class JudgmentSource(StrEnum):
    """판정이 어디서 왔는지."""

    ORIGINAL = "original"
    # 오답 찾기가 고른 하드 네거티브
    MINED = "mined"
    # 질의 만들기가 만든 정답
    SYNTHETIC = "synthetic"
    # 도우미가 바꾼 판정
    HELPER = "helper"
    # 사람이 바꾼 판정
    HUMAN = "human"


class ExcludeReason(StrEnum):
    """질의를 학습에서 뺀 이유. 비어 있으면(None) 학습에 쓴다."""

    MANUAL = "manual"
    DUPLICATE = "duplicate"
    HELPER = "helper"


class AnalysisStatus(StrEnum):
    """뜻 분석 한 번의 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# 만드는 중으로 보는 뜻 분석 상태. 데이터셋마다 한 번에 하나만 돈다.
ANALYSIS_BUILDING_STATUSES = (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING)


class ItemKind(StrEnum):
    """지도의 점 · 근접 중복이 질의인지 문서인지."""

    QUERY = "query"
    DOCUMENT = "document"


class SuggestionKind(StrEnum):
    """뜻 분석이 사람에게 묻는 판정 바꾸기."""

    # 오답인데 정답만큼 가깝고 Jev가 '답을 담음' → 정답으로
    FALSE_NEGATIVE = "false_negative"
    # 판정 없는 상위 문서를 Jev가 '답을 담음' → 정답으로
    MISSING_POSITIVE = "missing_positive"
    # 정답인데 멀고 Jev가 '아니오' → 떼기
    SUSPECT_POSITIVE = "suspect_positive"


class SuggestionDecision(StrEnum):
    """제안을 사람이 어떻게 했는지. 아직 안 했으면 비운다(대기)."""

    ACCEPTED = "accepted"
    # 지금 판정이 맞다. 다음 뜻 분석에서도 다시 묻지 않는다.
    KEPT = "kept"


class RepeatKind(StrEnum):
    """반복 구간의 종류."""

    # 여러 문서에 되풀이되는 문장 (숫자만 다른 것은 같은 틀)
    SENTENCE = "sentence"
    # 메타 모양 (이메일 · 웹 주소 · 전화 · 저작권 표시 · 전재 금지)
    META = "meta"


class RepeatDecision(StrEnum):
    """반복 구간을 어떻게 할지. 아직 고르지 않았으면 비운다."""

    # 학습 글에서 뗀다 (본문은 그대로, 내보낼 코퍼스와 규칙에도 적는다)
    REMOVE = "remove"
    # 남긴다 (내용이다). 청크의 대부분이면 질의를 만들지 않는다.
    KEEP = "keep"


class HelperTarget(StrEnum):
    """도우미가 바꾼 대상."""

    QUERY = "query"
    DOCUMENT = "document"
    JUDGMENT = "judgment"


class HelperField(StrEnum):
    """도우미가 바꾼 칸. 되돌릴 때 이 칸을 전 값으로 돌린다."""

    # 질의 학습 제외 (전 · 후 = 뺀 이유)
    EXCLUDE = "exclude"
    # 휴지통 (전 · 후 = 휴지통 여부 true/false)
    TRASH = "trash"
    # 글 (전 · 후 = 글). 글자 정리 · 질의 고치기
    TEXT = "text"
    # 판정 등급 (전 · 후 = 등급, 없으면 null)
    GRADE = "grade"
    # 질의를 만들지 않는 청크 표시 (전 · 후 = true/false)
    SKIP_GENERATION = "skip_generation"
    # 나눈 원문 (되돌리면 청크를 휴지통에 넣고 원문을 되살린다)
    CHUNKED = "chunked"
    # 새로 만든 질의 · 판정 (되돌리면 휴지통 · 지움)
    CREATED = "created"


class Query(Base):
    """질의 한 건: 글, 출처, 학습 제외 이유, 원본의 남는 필드."""

    __tablename__ = "retrieval_queries"
    __table_args__ = (
        CheckConstraint(check_in("source", QuerySource), name="source"),
        CheckConstraint(check_in("exclude_reason", ExcludeReason), name="exclude_reason"),
        # 같은 질의 찾기(중복)는 데이터셋 안에서 해시로 묶는다.
        Index("ix_retrieval_queries_same_text", "dataset_id", "text_hash"),
        # 가져오기를 다시 돌릴 때 그 가져오기가 넣은 줄을 지운다.
        Index("ix_retrieval_queries_dataset_id_import_id", "dataset_id", "import_id"),
    )

    # 질의 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 질의도 함께 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 이 질의를 넣은 가져오기 번호. 만들었거나 기록이 지워졌으면 비어 있다.
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_imports.id", ondelete="SET NULL")
    )

    # 질의 글 (앞뒤 공백을 뺀 것)
    text: Mapped[str] = mapped_column(Text)

    # 같은 질의를 찾는 열쇠 (정규화한 글의 sha256)
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 출처: original · synthetic · human
    source: Mapped[str] = mapped_column(
        String(ENUM_MAX_LENGTH), server_default=QuerySource.ORIGINAL.value
    )

    # 합성 질의면 그것을 만든 문서. 문서를 영구히 지우면 비운다. 문서마다 만든 질의를 찾는다.
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="SET NULL"), index=True
    )

    # 답 근거 (MRC의 답). 문서 나누기와 정답 확인에 쓴다.
    answer: Mapped[str | None] = mapped_column(Text)

    # 학습에서 뺀 이유: manual · duplicate · helper. 비어 있으면 학습에 쓴다.
    exclude_reason: Mapped[str | None] = mapped_column(String(ENUM_MAX_LENGTH))

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


class Document(Base):
    """문서(청크) 한 건: 코퍼스의 글. 같은 본문은 하나로 합친다."""

    __tablename__ = "retrieval_documents"
    __table_args__ = (
        # 정답과 같은 오답 찾기는 본문 해시로, 같은 문서 찾기(중복 · 가져올 때 합치기)는 학습 글 해시로 한다.
        Index("ix_retrieval_documents_same_text", "dataset_id", "text_hash"),
        Index("ix_retrieval_documents_same_input", "dataset_id", "input_hash"),
        Index("ix_retrieval_documents_dataset_id_import_id", "dataset_id", "import_id"),
    )

    # 문서 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 문서도 함께 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 이 문서를 넣은 가져오기 번호
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_imports.id", ondelete="SET NULL")
    )

    # 원본의 문서 번호 칸 (내보낼 때 쓴다)
    doc_key: Mapped[str | None] = mapped_column(String(DOC_KEY_MAX_LENGTH))

    # 제목. 없으면 빈 문자열.
    title: Mapped[str] = mapped_column(Text, server_default="")

    # 머리말 칸 값 (가져올 때 고른 칸들의 값을 ' · '로 이은 것). 없으면 빈 문자열.
    header: Mapped[str] = mapped_column(Text, server_default="")

    # 나누기가 찾은 구획 경로 (제목 › 제목). 나누지 않았거나 구조가 없으면 빈 문자열.
    section: Mapped[str] = mapped_column(Text, server_default="")

    # 묶음 칸 값 (가져올 때 고른 칸). 나중에 train · test로 나눌 때 같은 값끼리 한쪽에 두려고 남긴다.
    group_key: Mapped[str | None] = mapped_column(String(GROUP_KEY_MAX_LENGTH))

    # 본문
    text: Mapped[str] = mapped_column(Text)

    # 본문의 열쇠 (정규화한 본문의 sha256). 정답과 같은 오답을 찾는다.
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 학습 글이 본문과 다를 때의 학습 글 (머리말 · 뗀 구간). 같으면 비운다(글을 두 번 두지 않으려고).
    input_text: Mapped[str | None] = mapped_column(Text)

    # 학습 글의 열쇠. 임베딩 캐시 · 같은 문서 찾기에 쓴다.
    input_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 학습 글의 토큰 수 어림 (text_rules.estimate_tokens). 긴 문서 검사와 나누기에 쓴다.
    token_count: Mapped[int] = mapped_column(Integer, server_default="0")

    # 글을 넣거나 바꿀 때 한 번 재 둔 표시 {"broken": true, "pick": "표만"} (진단이 요청마다 글을 다시 읽지 않게)
    marks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 나누기로 생긴 청크면 원문 문서. 원문은 지우지 않고 replaced_at으로 가린다. 원문마다 청크를 찾는다.
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="SET NULL"), index=True
    )

    # 청크 순서 (0부터). 원문이면 비어 있다.
    chunk_index: Mapped[int | None] = mapped_column(Integer)

    # 나누기로 청크가 대신한 시각. 비어 있으면 코퍼스에 쓴다.
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 질의를 만들지 않는 청크 (목차 · 표만 · 서명란). 코퍼스에는 남는다.
    skip_generation: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))

    # 휴지통에 넣은 시각
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 고칠 때마다 1씩 오르는 번호
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")

    # 원본에서 쓰지 않은 남는 필드들
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 만든 시각 · 고친 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def training_text(self) -> str:
        """학습 글 (본문과 같으면 본문)."""
        return self.input_text if self.input_text is not None else self.text


class Judgment(Base):
    """판정 한 줄: (질의, 문서) 한 쌍의 등급과 출처."""

    __tablename__ = "retrieval_judgments"
    __table_args__ = (
        CheckConstraint(f"grade BETWEEN {GRADE_MIN} AND {GRADE_MAX}", name="grade"),
        CheckConstraint(check_in("source", JudgmentSource), name="source"),
        # 데이터셋별 정답 · 오답 수를 센다.
        Index("ix_retrieval_judgments_dataset_id_grade", "dataset_id", "grade"),
    )

    # 질의 번호. 질의를 지우면 판정도 지운다.
    query_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_queries.id", ondelete="CASCADE"), primary_key=True
    )

    # 문서 번호. 문서를 지우면 판정도 지운다. 문서마다 쓰는 질의를 찾는다.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    # 데이터셋 번호 (세기 · 거르기를 빨리 하려고 적어 둔다)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 등급 0~3 (0 = 오답, 1 이상 = 정답)
    grade: Mapped[int] = mapped_column(SmallInteger)

    # 출처: original · mined · synthetic · helper · human
    source: Mapped[str] = mapped_column(
        String(ENUM_MAX_LENGTH), server_default=JudgmentSource.ORIGINAL.value
    )

    # 같은 쌍이 원본에서 정답이자 오답으로 나왔는지 (판정 충돌). 사람 · 도우미가 정하면 끈다.
    conflict: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))

    # 교사 점수 (Jev가 매긴 '답을 담나' 확률). 지식 증류로 내보낼 때 쓴다.
    teacher_score: Mapped[float | None] = mapped_column(REAL)

    # 질의의 글자 두 개 묶음 가운데 문서에도 있는 몫 (0~1, text_rules.lexical_overlap). 쉬운 쌍을 SQL로 찾으려고 넣을 때 적는다.
    overlap: Mapped[float | None] = mapped_column(REAL)

    # 이 판정을 넣은 가져오기 번호
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_imports.id", ondelete="SET NULL")
    )

    # 만든 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetSettings(Base):
    """검색 데이터셋의 설정: 들어온 모양(입구), 학습할 모델, 길이 · 오답 수 · 오답 찾기 기준."""

    __tablename__ = "retrieval_dataset_settings"
    __table_args__ = (CheckConstraint(check_in("shape", Shape), name="shape"),)

    # 데이터셋 번호. 데이터셋을 지우면 설정도 지운다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), primary_key=True
    )

    # 처음 가져온 원본의 모양 (입구 단계를 정한다)
    shape: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 학습할 모델 (오답 찾기 · 기준 검색 · 길이에 쓴다). 비어 있으면 연결된 임베딩 모델.
    target_model: Mapped[str | None] = mapped_column(String(MODEL_NAME_MAX_LENGTH))

    # 질의 · 문서 최대 토큰 (넘으면 잘린다)
    query_max_tokens: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_QUERY_MAX_TOKENS)
    )
    doc_max_tokens: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_DOC_MAX_TOKENS))

    # 질의마다 오답 수
    negatives: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_NEGATIVES))

    # 오답 찾기 순위 범위 (이 순위 안에서 고른다)
    mine_rank_from: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_MINE_RANK_FROM))
    mine_rank_to: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_MINE_RANK_TO))

    # 거짓 오답 경계: 정답 유사도의 이 배를 넘는 문서는 오답으로 고르지 않는다
    mine_margin: Mapped[float] = mapped_column(REAL, server_default=str(DEFAULT_MINE_MARGIN))

    # 질의마다 모아 두는 오답 풀 (학습 jsonl에 모두 넣고 학습기가 매번 오답 수만큼 고른다)
    negative_pool: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_NEGATIVE_POOL))

    # 학습 글 머리말에 구획 경로를 붙이는지
    section_header: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))

    # 질의 만들기의 되찾기 거르기를 쓰는지 (코퍼스가 아주 작으면 끈다)
    round_trip: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))

    # 쉬운 쌍 상한: 학습에 쓰는 질의 가운데 이 몫까지만 둔다
    easy_pair_cap: Mapped[float] = mapped_column(REAL, server_default=str(DEFAULT_EASY_PAIR_CAP))

    # 문서 나누기의 오버랩: 앞 청크 끝의 이 토큰만큼(문장 단위)을 다음 청크 앞에 겹친다. 0이면 겹치지 않는다.
    chunk_overlap: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_CHUNK_OVERLAP))

    # 질의 만들기: 청크마다 남길 질의 수 (후보는 두 배를 만들고, 모자라면 한 번 더 만든다)
    queries_per_chunk: Mapped[int] = mapped_column(
        Integer, server_default=str(DEFAULT_QUERIES_PER_CHUNK)
    )

    # 질의 만들기: 후보 가운데 질문형의 몫(%). 나머지는 검색어형이다.
    question_share: Mapped[int] = mapped_column(Integer, server_default=str(DEFAULT_QUESTION_SHARE))

    # 첫 뜻 분석의 기준 검색 점수 {recall_at_10, mrr_at_10} (정제 전 → 후를 견준다)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 가장 최근 오답 훑기 결과 (순위 묶음 × 문턱의 거짓 오답 비율). 없으면 빈 객체.
    negative_scan: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 반복 구간을 마지막으로 살핀 시각 (없으면 아직 안 살핌)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 모든 질의의 오답을 마지막으로 찾을 때(처음 찾기 · 다시 찾기) 코퍼스의 가장 큰 문서 번호.
    # 그 뒤 번호의 코퍼스 문서가 생기면(데이터 추가 · 나누기) 문서 풀이 늘어 오답을 다시 찾는다. 찾은 적이 없으면 비어 있다.
    mined_document_id: Mapped[int | None] = mapped_column(BigInteger)

    # 고친 시각
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Repeat(Base):
    """반복 구간 하나(틀): 여러 문서에 되풀이되는 문장, 또는 메타 모양 한 종류. 사람이 떼기 · 남김을 고른다.

    다시 살피면 같은 열쇠의 결정은 이어 간다. 문서마다 든 반복 구간은 retrieval_document_repeats에 적는다.
    """

    __tablename__ = "retrieval_repeats"
    __table_args__ = (
        CheckConstraint(check_in("kind", RepeatKind), name="kind"),
        CheckConstraint(check_in("suggestion", RepeatDecision), name="suggestion"),
        CheckConstraint(check_in("decision", RepeatDecision), name="decision"),
        UniqueConstraint("dataset_id", "key"),
    )

    # 반복 구간 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 함께 지운다. 데이터셋별로 읽는다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 열쇠: 문장이면 뼈대(숫자 → 0)의 해시, 메타 모양이면 meta:<종류>
    key: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 종류: sentence · meta
    kind: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 대표 글 (가장 먼저 본 원문, 메타 모양이면 종류 이름)
    text: Mapped[str] = mapped_column(Text)

    # 보기 원문 몇 개 (숫자 · 이름만 다른 것)
    samples: Mapped[list[str]] = mapped_column(JSONB, server_default=sql_text("'[]'::jsonb"))

    # 든 문서 수 · 그 가운데 앞머리 · 꼬리에 있는 문서 수
    document_count: Mapped[int] = mapped_column(Integer, server_default="0")
    head_count: Mapped[int] = mapped_column(Integer, server_default="0")
    tail_count: Mapped[int] = mapped_column(Integer, server_default="0")

    # 규칙 · 도우미의 제안 (메타 모양 · 메타가 든 문장은 떼기, 나머지는 남김)과 그 까닭
    suggestion: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))
    reason: Mapped[str | None] = mapped_column(Text)

    # 사람의 결정: remove · keep. 아직이면 비운다.
    decision: Mapped[str | None] = mapped_column(String(ENUM_MAX_LENGTH))

    # 결정한 시각 · 살핀 시각
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentRepeat(Base):
    """문서 하나에 든 반복 구간 하나 (문제 거르기 · 결정을 바꿀 때 다시 만들 학습 글을 찾는다)."""

    __tablename__ = "retrieval_document_repeats"

    # 문서 번호. 문서를 지우면 함께 지운다.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE"), primary_key=True
    )

    # 반복 구간 번호. 반복 구간별로 든 문서를 찾는다.
    repeat_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_repeats.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Analysis(Base):
    """뜻 분석 한 번: 질의 · 문서를 임베딩해 지도 · 근접 중복 · 기준 검색 순위 · 제안을 만든다.

    데이터셋마다 다 만든 분석은 가장 최근 것 하나만 남긴다. 만드는 중인 것도 하나뿐이다(부분 UNIQUE).
    """

    __tablename__ = "retrieval_analyses"
    __table_args__ = (
        CheckConstraint(check_in("status", AnalysisStatus), name="status"),
        Index(
            "uq_retrieval_analyses_building",
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

    # 좌표 · 분석을 만든 방법과 값
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 분석 결과 요약: 기준 검색 점수 · 근접 중복 · 제안 · 주제 무리 · 쉬운 오답 (semantic.py가 만든다)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 분석한 질의 · 문서 수 (휴지통 밖)
    query_count: Mapped[int] = mapped_column(BigInteger, server_default="0")
    document_count: Mapped[int] = mapped_column(BigInteger, server_default="0")

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
    """지도의 점 하나: 질의 또는 문서 한 건의 2차원 좌표와 주제 무리."""

    __tablename__ = "retrieval_map_points"
    __table_args__ = (
        CheckConstraint(check_in("kind", ItemKind), name="kind"),
        # 질의 · 문서를 지울 때 DB가 이 칸으로 점을 찾는다.
        Index("ix_retrieval_map_points_query_id", "query_id"),
        Index("ix_retrieval_map_points_document_id", "document_id"),
    )

    # 점 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 분석 번호. 분석을 지우면 점도 지운다. 분석별로 점을 읽는다.
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_analyses.id", ondelete="CASCADE"), index=True
    )

    # 질의인지 문서인지
    kind: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 질의 번호 (질의 점). 질의를 지우면 점도 지운다.
    query_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_queries.id", ondelete="CASCADE")
    )

    # 문서 번호 (문서 점)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE")
    )

    # 좌표 [-1, 1]
    x: Mapped[float] = mapped_column(REAL)
    y: Mapped[float] = mapped_column(REAL)

    # 주제 무리 번호 (문서로 무리를 나누고 질의는 가장 가까운 무리)
    cluster: Mapped[int] = mapped_column(SmallInteger, server_default="0")


class Ranking(Base):
    """기준 검색의 한 줄: 질의로 코퍼스를 찾았을 때 한 문서의 순위와 유사도.

    질의마다 상위 RANKING_TOP_K와, 판정이 있는 문서(순위가 더 멀어도)를 적는다.
    """

    __tablename__ = "retrieval_rankings"

    # 분석 번호
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_analyses.id", ondelete="CASCADE"), primary_key=True
    )

    # 질의 번호. 질의를 지우면 순위도 지운다.
    query_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_queries.id", ondelete="CASCADE"), primary_key=True
    )

    # 문서 번호. 문서를 지울 때 DB가 이 칸으로 찾는다.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    # 순위 (1부터)
    rank: Mapped[int] = mapped_column(Integer)

    # 코사인 유사도
    similarity: Mapped[float] = mapped_column(REAL)


class NearDuplicate(Base):
    """근접 중복 한 쌍: 글자는 달라도 뜻이 거의 같은 두 질의(또는 두 문서). 작은 번호 · 큰 번호 순서로 한 번만 적는다."""

    __tablename__ = "retrieval_near_duplicates"
    __table_args__ = (CheckConstraint(check_in("kind", ItemKind), name="kind"),)

    # 분석 번호
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_analyses.id", ondelete="CASCADE"), primary_key=True
    )

    # 질의 쌍인지 문서 쌍인지
    kind: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH), primary_key=True)

    # 앞 번호 · 뒤 번호 (질의 번호 또는 문서 번호, 작은 것이 앞)
    item_a: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_b: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # 코사인 유사도
    similarity: Mapped[float] = mapped_column(REAL)


class Suggestion(Base):
    """제안 한 건: 뜻 분석이 사람에게 묻는 판정 바꾸기(거짓 오답 · 빠진 정답 · 정답 의심).

    같은 (종류, 질의, 문서)에 사람이 '유지'했으면 다음 분석에서도 유지로 이어 간다.
    """

    __tablename__ = "retrieval_suggestions"
    __table_args__ = (
        CheckConstraint(check_in("kind", SuggestionKind), name="kind"),
        CheckConstraint(check_in("decision", SuggestionDecision), name="decision"),
        Index("ix_retrieval_suggestions_query_id", "query_id"),
        Index("ix_retrieval_suggestions_document_id", "document_id"),
    )

    # 제안 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 분석 번호. 분석별로 제안을 읽는다.
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_analyses.id", ondelete="CASCADE"), index=True
    )

    # 종류: false_negative · missing_positive · suspect_positive
    kind: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 질의 · 문서. 지우면 제안도 지운다.
    query_id: Mapped[int] = mapped_column(ForeignKey("retrieval_queries.id", ondelete="CASCADE"))
    document_id: Mapped[int] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE")
    )

    # 기준 검색 순위 · 유사도
    rank: Mapped[int] = mapped_column(Integer)
    similarity: Mapped[float] = mapped_column(REAL)

    # Jev가 매긴 '답을 담나' 예 확률. Jev에 묻지 못했으면 비운다.
    jev_probability: Mapped[float | None] = mapped_column(REAL)

    # Jev가 확인했는지 (예 ≥ 기준, 정답 의심은 아니오 ≥ 기준)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("false"))

    # 사람의 판단: accepted · kept. 아직이면 비운다.
    decision: Mapped[str | None] = mapped_column(String(ENUM_MAX_LENGTH))

    # 판단한 시각
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HelperChange(Base):
    """LLM 도우미가 질의 · 문서 · 판정 하나를 바꾼 기록. 사건(바꾼 카드) 하나에 여러 줄이 붙고, 카드 · 실행 단위로 되돌린다.

    되돌릴 때 지금 값이 after와 같을 때만 before로 돌린다(그 사이 사람이 고친 것은 건드리지 않는다).
    """

    __tablename__ = "retrieval_helper_changes"
    __table_args__ = (
        CheckConstraint(check_in("target", HelperTarget), name="target"),
        CheckConstraint(check_in("field", HelperField), name="field"),
    )

    # 기록 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 도우미 실행 번호. 실행별로 되돌린다.
    run_id: Mapped[int] = mapped_column(
        ForeignKey("system_helper_runs.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 카드(사건) 번호. 카드 하나를 되돌릴 때 찾는다.
    event_id: Mapped[int] = mapped_column(
        ForeignKey("system_helper_events.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 대상: query · document · judgment
    target: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 질의 번호 (질의 · 판정). 질의를 영구히 지우면 기록도 지운다.
    query_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_queries.id", ondelete="CASCADE"), index=True
    )

    # 문서 번호 (문서 · 판정)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("retrieval_documents.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 칸
    field: Mapped[str] = mapped_column(String(ENUM_MAX_LENGTH))

    # 전 · 후 값
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # 되돌린 시각
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
