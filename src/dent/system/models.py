"""시스템 층 테이블 (system_). 모듈 종류와 상관없이 모든 모듈이 쓰는 바탕이다.

- system_jobs: 작업 대기열
- system_embedding_models: 쓰는 임베딩 모델
- system_embeddings: 문장 임베딩 캐시. (모델, 문장 해시)마다 한 줄이고 모든 데이터셋 · 모듈이 같이 쓴다
- system_files: 올린 파일 목록
- system_datasets: 데이터셋 목록 (모든 모듈)
- system_imports: 가져오기 기록 (모든 모듈)
- system_connections: 외부 모델 서버 연결 (임베딩 · Jev · LLM). 화면의 연결 설정에서 저장한다
- system_helper_runs: LLM 도우미 실행 한 번 (단계 · 쓴 토큰 · 바꾼 수 · 허락을 묻는 계획)
- system_helper_events: 도우미 실행의 사건 기록 (에이전트의 말 · 도구 카드 · 결과). 화면이 이어 읽는다
- system_exports: 내보낸 파일 (모듈이 모양을 정해 쓰고, 시스템이 보관 · 내려받기 · 정리를 한다)

모듈 테이블은 이 테이블을 참조해도 되지만, 시스템 테이블은 모듈 테이블을 모른다.
상태 같은 고정된 값은 text + CHECK 제약으로 저장한다.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dent.system.db import Base, check_in
from dent.system.text import TEXT_HASH_LENGTH

# 모듈 이름(classification 등)과 작업 종류(import 등)의 최대 길이
MODULE_NAME_MAX_LENGTH = 32
JOB_KIND_MAX_LENGTH = 32

# 상태·가져온 곳 값의 최대 길이
STATUS_MAX_LENGTH = 16

# 임베딩 모델 이름의 최대 길이
MODEL_NAME_MAX_LENGTH = 200

# 외부 서버 API 키의 최대 길이 (OpenAI 프로젝트 키가 160자 안팎이라 넉넉히)
API_KEY_MAX_LENGTH = 1_000

# 데이터셋 이름의 최대 길이
DATASET_NAME_MAX_LENGTH = 200

# 가져온 곳 이름(파일 이름, "저장소 · 구성")의 최대 길이
SOURCE_NAME_MAX_LENGTH = 500

# 올린 파일 번호의 길이. uuid4를 16진수로 쓰면 32자다.
UPLOAD_ID_LENGTH = 32

# sha256을 16진수로 쓴 길이
SHA256_HEX_LENGTH = 64

# JSONB 칼럼의 DB 기본값
EMPTY_JSON_OBJECT = text("'{}'::jsonb")
EMPTY_JSON_ARRAY = text("'[]'::jsonb")


class JobStatus(StrEnum):
    """작업 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class ImportSource(StrEnum):
    """가져온 곳."""

    FILE = "file"
    HUGGINGFACE = "huggingface"


class ImportStatus(StrEnum):
    """가져오기 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# 가져오는 중으로 보는 가져오기 상태. 이 데이터셋은 지우지 않는다.
IMPORTING_STATUSES = (ImportStatus.QUEUED, ImportStatus.RUNNING)


class ConnectionRole(StrEnum):
    """외부 모델 서버의 역할. 역할마다 연결은 하나다."""

    EMBEDDING = "embedding"
    JEV = "jev"
    LLM = "llm"


class HelperRunStatus(StrEnum):
    """도우미 실행 한 번의 상태."""

    # 작업이 기다리거나 도는 중
    RUNNING = "running"
    # 고칠 것을 다 고치고 새 문장 만들기 허락을 기다림
    ASKING = "asking"
    DONE = "done"
    # 사람이 멈춤
    STOPPED = "stopped"
    FAILED = "failed"


class HelperEventKind(StrEnum):
    """도우미 사건의 종류. 화면이 종류마다 다른 카드로 그린다."""

    # 에이전트의 말 (스스로 묻는 말은 payload.ask)
    SAY = "say"
    # Jev 판정 표
    JEV = "jev"
    # LLM이 직접 본 문장과 판단
    LLM = "llm"
    # 데이터를 바꿈 (되돌리기 단위)
    CHANGE = "change"
    # 검사 값이 바뀜 (16 → 0)
    RESULT = "result"
    # 사람에게 넘김 (보류)
    HOLD = "hold"
    # 새 문장 만들기 허락을 물음
    PERMISSION = "permission"
    # 도우미 밖의 알림 (연결 없음 · 상한 · 멈춤 · 실패)
    NOTICE = "notice"
    # 실행 한 번(또는 AI로 고치기 한 번)의 보고: 종합 전 → 후 · 고친 검사 · 바꿈 · 비용
    REPORT = "report"


class ExportStatus(StrEnum):
    """내보내기 한 번의 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# 내보낸 파일 이름 · 형식 이름의 최대 길이
EXPORT_FILE_NAME_MAX_LENGTH = 300
EXPORT_FORMAT_MAX_LENGTH = 32


class LlmProvider(StrEnum):
    """LLM 공급자. 부르는 방식(헤더 · 주소)이 공급자마다 다르다."""

    OPENAI = "openai"
    VLLM = "vllm"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    XAI = "xai"


class Job(Base):
    """작업 대기열의 한 줄. API 서버가 넣고 작업 실행기가 꺼내 처리한다."""

    # 이 모델이 저장되는 테이블 이름. 시스템 층 테이블은 system_으로 시작한다.
    __tablename__ = "system_jobs"
    __table_args__ = (
        CheckConstraint(check_in("status", JobStatus), name="status"),
        # 다음 작업을 빨리 꺼내려고 대기 중인 줄만 담는 부분 인덱스를 건다.
        Index("ix_system_jobs_queued", "id", postgresql_where=text("status = 'queued'")),
    )

    # 작업 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 작업을 넣은 모듈 (예: classification). 시스템이 넣은 작업은 system.
    # 모듈 테이블을 참조하지 않으려고 외래 키가 아닌 이름으로 둔다.
    module: Mapped[str] = mapped_column(String(MODULE_NAME_MAX_LENGTH))

    # 작업 종류 (예: import). 작업 실행기는 (module, kind)로 처리 함수를 고른다.
    kind: Mapped[str] = mapped_column(String(JOB_KIND_MAX_LENGTH))

    # 작업이 다루는 데이터셋. 없으면(정리 작업 등) 비운다. 데이터셋을 지워도 작업 기록은 남기고 번호만 비운다.
    # 작업 기록 화면이 데이터셋별로 거른다.
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="SET NULL"), index=True
    )

    # 넣을 때의 데이터셋 이름. 데이터셋을 지운 뒤에도 작업 기록에 이름이 보이게 적어 둔다.
    dataset_name: Mapped[str | None] = mapped_column(String(DATASET_NAME_MAX_LENGTH))

    # 상태. JobStatus 값 가운데 하나.
    status: Mapped[str] = mapped_column(
        String(STATUS_MAX_LENGTH), server_default=JobStatus.QUEUED.value
    )

    # 작업에 넘길 값
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 처리한 양. 화면의 진행률이 된다.
    progress_done: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 전체 양. 모르면 비운다.
    progress_total: Mapped[int | None] = mapped_column(BigInteger)

    # 결과 요약
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # 실패 이유. 사용자에게 보여줄 한국어 문장이다.
    error: Mapped[str | None] = mapped_column(Text)

    # 끊겨서 다시 시작한 횟수. 너무 많으면 실패로 둔다.
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")

    # 사용자가 멈추라고 했는지. 작업 실행기가 묶음마다 확인한다.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    # 작업 실행기가 이 작업을 처리하며 살아 있다고 알린 마지막 시각
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 일어난 일을 시각 순으로 쌓는다: 시작 · 단계 · 다시 보내기 · 쓴 연결. result와 달리 덮어쓰지 않는다.
    # 한 줄은 {"type": ..., "at": ISO 시각, ...}. 작업 기록 화면의 단계 · 다시 보내기가 이것을 읽는다.
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_JSON_ARRAY)

    # 넣은 시각. 저장할 때 DB가 현재 시각을 넣는다.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 시작한 시각
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 끝난 시각
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmbeddingModel(Base):
    """쓰는 임베딩 모델. 같은 이름인데 차원이 바뀌면 저장한 임베딩과 섞이지 않게 막는다."""

    __tablename__ = "system_embedding_models"

    # 모델 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 모델 이름. 외부 서버에 보내는 이름 그대로이고, 겹치지 않는다.
    name: Mapped[str] = mapped_column(String(MODEL_NAME_MAX_LENGTH), unique=True)

    # 벡터 차원. 처음 연결을 확인할 때 돌려받은 벡터 길이로 적는다.
    dim: Mapped[int] = mapped_column(Integer)

    # 등록한 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Embedding(Base):
    """문장 하나의 임베딩 벡터. (모델, 문장 해시)마다 한 번만 계산해 모든 데이터셋 · 모듈이 같이 쓴다.

    벡터는 반 정밀도(halfvec, 한 칸 2바이트)로 둔다. 저장 공간이 float4의 절반이고, 지도 · 근접 중복에는
    이 정밀도로 충분하다. 모델마다 차원이 달라서 차원을 정하지 않은 halfvec 칸을 쓴다(차원은 모델 줄에 있다).
    가까운 벡터 찾기 인덱스(ANN)는 아직 없다.
    """

    __tablename__ = "system_embeddings"

    # 모델 번호. 모델 줄이 지워지면 그 모델의 벡터도 쓸 데가 없으므로 함께 지운다.
    # (모델, 문장 해시)가 번호라서 모델별로 해시를 찾는 인덱스가 저절로 생긴다.
    model_id: Mapped[int] = mapped_column(
        ForeignKey("system_embedding_models.id", ondelete="CASCADE"), primary_key=True
    )

    # 문장 해시(text.make_text_hash). 같은 문장이면 데이터셋 · 모듈이 달라도 같은 값이다.
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH), primary_key=True)

    # 임베딩 벡터 (반 정밀도). 길이는 모델의 dim과 같다.
    vector: Mapped[Any] = mapped_column(HALFVEC())

    # 계산한 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class File(Base):
    """올린 파일 한 건. 파일은 storage 아래에 두고, 여기에는 어디에 무엇을 두었는지 적는다.

    원본 파일은 가져오기가 끝나면 지운다. 이 기록(이름·크기·해시)은 어디서 가져왔는지 알려고 남긴다.
    """

    __tablename__ = "system_files"

    # 올린 파일 번호(upload_id). uuid4 16진수 32자이고, 저장 폴더 이름으로도 쓴다.
    id: Mapped[str] = mapped_column(String(UPLOAD_ID_LENGTH), primary_key=True)

    # 사용자가 올린 원래 파일 이름
    original_name: Mapped[str] = mapped_column(Text)

    # storage 폴더 기준의 저장 경로 (예: uploads/2026/09/{upload_id}/data.csv)
    stored_path: Mapped[str] = mapped_column(Text)

    # 파일 크기(바이트)
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    # 파일 내용의 sha256. 같은 파일을 다시 올렸는지 이 값으로 찾으므로 인덱스를 건다.
    sha256: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), index=True)

    # 올린 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 원본 파일을 지운 시각. 아직 있으면 비운다.
    # 가져오기가 끝나거나(성공·실패), 가져오지 않은 채 오래되면 지운다.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Dataset(Base):
    """데이터셋 하나. 어느 모듈의 것인지 적고, 그 안의 데이터는 모듈 테이블이 가진다."""

    __tablename__ = "system_datasets"
    # 이름은 모듈 안에서 겹치지 않는다. (module, name) 인덱스로 모듈별 목록도 찾는다.
    __table_args__ = (UniqueConstraint("module", "name"),)

    # 데이터셋 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 이 데이터셋을 가진 모듈 (예: classification)
    module: Mapped[str] = mapped_column(String(MODULE_NAME_MAX_LENGTH))

    # 이름
    name: Mapped[str] = mapped_column(String(DATASET_NAME_MAX_LENGTH))

    # 설명. 없으면 빈 문자열.
    description: Mapped[str] = mapped_column(Text, server_default="")

    # 만든 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 마지막으로 바뀐 시각. 이름뿐 아니라 모듈의 데이터가 바뀌어도 service가 새로 적는다.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Import(Base):
    """가져오기 한 번의 기록. 어디서 무엇을 어떻게 가져왔고, 몇 줄을 넣고 몇 줄을 건너뛰었는지."""

    __tablename__ = "system_imports"
    __table_args__ = (
        CheckConstraint(check_in("source", ImportSource), name="source"),
        CheckConstraint(check_in("status", ImportStatus), name="status"),
    )

    # 가져오기 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 가져온 데이터셋 번호. 데이터셋을 지우면 기록도 지운다. 데이터셋별 가져오기 목록을 자주 본다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 가져온 곳: file · huggingface
    source: Mapped[str] = mapped_column(String(STATUS_MAX_LENGTH))

    # 화면에 보여 줄 가져온 곳 이름. 파일 이름이나 "저장소 · 구성"
    source_name: Mapped[str] = mapped_column(String(SOURCE_NAME_MAX_LENGTH))

    # 가져오기 설정: 올린 파일 번호·시트나 저장소·구성·분할, 그리고 모듈의 필드 맞춤
    options: Mapped[dict[str, Any]] = mapped_column(JSONB)

    # 상태: queued · running · done · failed
    status: Mapped[str] = mapped_column(
        String(STATUS_MAX_LENGTH), server_default=ImportStatus.QUEUED.value
    )

    # 이 가져오기를 처리하는 작업 번호. 오래된 작업 기록이 지워지면 비운다.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("system_jobs.id", ondelete="SET NULL"))

    # 원본의 전체 줄 수. 읽기 전에는 모른다.
    rows_total: Mapped[int | None] = mapped_column(BigInteger)

    # 넣은 줄 수
    rows_added: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 건너뛴 줄 수
    rows_skipped: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 건너뛴 줄과 이유 [{line, reason}]. 너무 많아지지 않게 앞의 몇 줄만 남긴다.
    skipped_lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=EMPTY_JSON_ARRAY
    )

    # 모듈만의 결과 (예: 분류의 new_labels)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 실패 이유. 사용자에게 보여줄 한국어 문장이다.
    error: Mapped[str | None] = mapped_column(Text)

    # 만든 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 끝난 시각
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connection(Base):
    """외부 모델 서버 연결 하나. 화면의 연결 설정에서 저장하고, 다시 실행하지 않아도 바로 쓴다.

    연결 값은 이 테이블에만 둔다(.env에 두지 않는다). 줄이 없으면 그 역할은 미연결이다.
    """

    __tablename__ = "system_connections"
    __table_args__ = (
        CheckConstraint(check_in("role", ConnectionRole), name="role"),
        CheckConstraint(check_in("provider", LlmProvider), name="provider"),
    )

    # 역할: embedding · jev · llm. 역할마다 하나라서 이것이 번호다.
    role: Mapped[str] = mapped_column(Text, primary_key=True)

    # 서버 주소. http:// 또는 https://로 시작하고 끝에 /가 없다. 예: http://127.0.0.1:8010
    base_url: Mapped[str] = mapped_column(Text)

    # 모델 이름. 비어 있으면 서버가 고른다(Jev는 자동 고르기).
    model: Mapped[str | None] = mapped_column(Text)

    # LLM 공급자 (openai · vllm · anthropic · google · xai). LLM이 아니면 null
    provider: Mapped[str | None] = mapped_column(Text)

    # 서버 API 키. 없으면 null. 이 PC의 DB에만 두고, API 응답과 로그에는 앞뒤 몇 글자만 보인다.
    api_key: Mapped[str | None] = mapped_column(Text)

    # 마지막으로 저장한 시각
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HelperRun(Base):
    """LLM 도우미 실행 한 번: 진단을 읽고 단계마다 규칙 · Jev · LLM으로 데이터를 고친다.

    무엇을 어떻게 고치는지는 모듈이 정하고(단계 · 도구), 이 줄에는 모든 모듈이 같이 쓰는 진행 · 사용량만 둔다.
    바꾼 데이터의 전 · 후(되돌리기)는 모듈이 자기 표에 적는다.
    """

    __tablename__ = "system_helper_runs"
    __table_args__ = (
        CheckConstraint(check_in("status", HelperRunStatus), name="status"),
        # 데이터셋마다 도는 도우미는 하나뿐이다. 두 창에서 동시에 눌러도 둘이 돌지 않게 DB가 막는다.
        Index(
            "uq_system_helper_runs_running",
            "dataset_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    # 실행 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 모듈 이름. 예: classification
    module: Mapped[str] = mapped_column(String(MODULE_NAME_MAX_LENGTH))

    # 데이터셋 번호. 데이터셋을 지우면 실행 기록도 지운다. 데이터셋별로 마지막 실행을 찾는다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 이 실행을 처리하는 작업 번호. 오래된 작업 기록이 지워지면 비운다.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("system_jobs.id", ondelete="SET NULL"))

    # 상태: running · asking · done · stopped · failed
    status: Mapped[str] = mapped_column(
        String(STATUS_MAX_LENGTH), server_default=HelperRunStatus.RUNNING.value
    )

    # 단계 목록과 단계마다 상태 · 사실 (화면의 단계 줄). [{key, no, title, status, facts, delta, plan}]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=EMPTY_JSON_ARRAY)

    # 지금 단계 번호 (0 = 계획)
    step_now: Mapped[int] = mapped_column(Integer, server_default="0")

    # 쓴 LLM 모델 이름 (시작할 때의 연결)
    model: Mapped[str | None] = mapped_column(Text)

    # 쓴 LLM 토큰 (입력 + 출력), Jev에 물은 수, 바꾼 문장 수, 사람에게 넘긴 수
    llm_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    jev_calls: Mapped[int] = mapped_column(BigInteger, server_default="0")
    changed: Mapped[int] = mapped_column(BigInteger, server_default="0")
    held: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 사람이 [멈추기]를 눌렀는지. 도우미가 도구를 부르기 전마다 본다.
    stop_requested: Mapped[bool] = mapped_column(Boolean, server_default="false")

    # 허락을 묻는 새 문장 만들기 계획 (asking일 때). 예: {"targets": {"경제": 8}, "reason": …}
    permission: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # 실패 · 멈춤 이유. 사용자에게 보여줄 한국어 문장이다.
    error: Mapped[str | None] = mapped_column(Text)

    # 시작한 시각 · 끝난 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HelperEvent(Base):
    """도우미 실행의 사건 하나. 쌓기만 하고 고치지 않는다(되돌린 표시는 모듈의 변경 기록에 둔다)."""

    __tablename__ = "system_helper_events"
    __table_args__ = (CheckConstraint(check_in("kind", HelperEventKind), name="kind"),)

    # 사건 번호. 화면은 마지막으로 읽은 번호 다음부터 이어 읽는다.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 실행 번호. 실행을 지우면 사건도 지운다. 실행별로 번호 순서로 읽는다.
    run_id: Mapped[int] = mapped_column(
        ForeignKey("system_helper_runs.id", ondelete="CASCADE"), index=True
    )

    # 단계 번호 (0 = 계획)
    step: Mapped[int] = mapped_column(Integer)

    # 종류: say · jev · llm · change · result · hold · permission · notice
    kind: Mapped[str] = mapped_column(String(STATUS_MAX_LENGTH))

    # 종류마다 다른 내용 (화면이 그대로 그린다). 예: say {"text", "ask"}, change {"action", "count", "tool"}
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 적은 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Export(Base):
    """내보내기 한 번: 모듈이 정한 모양으로 만든 파일 하나(여럿이면 zip).

    파일은 storage/exports/{번호}/{파일 이름}에 두고, 보관 기간(exports.EXPORT_RETENTION)이 지나면 정리 작업이 지운다.
    기록은 남기고 파일을 지운 시각을 적는다.
    """

    __tablename__ = "system_exports"
    __table_args__ = (CheckConstraint(check_in("status", ExportStatus), name="status"),)

    # 내보내기 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 만든 모듈 (예: retrieval)
    module: Mapped[str] = mapped_column(String(MODULE_NAME_MAX_LENGTH))

    # 데이터셋 번호. 데이터셋을 지우면 기록도 지운다. 데이터셋별 내보내기 목록을 본다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 모듈이 정한 형식 이름 (예: train_jsonl · beir)
    format: Mapped[str] = mapped_column(String(EXPORT_FORMAT_MAX_LENGTH))

    # 모듈이 받은 선택 (넣을 출처 · 오답 수 · 교사 점수 …)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 상태: queued · running · done · failed
    status: Mapped[str] = mapped_column(
        String(STATUS_MAX_LENGTH), server_default=ExportStatus.QUEUED.value
    )

    # 내려받을 때의 파일 이름
    file_name: Mapped[str] = mapped_column(String(EXPORT_FILE_NAME_MAX_LENGTH))

    # 파일 크기(바이트). 만들기 전에는 null.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    # 모듈만의 결과 (예: 질의 · 판정 수)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 이 내보내기를 만드는 작업 번호. 오래된 작업 기록이 지워지면 비운다.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("system_jobs.id", ondelete="SET NULL"))

    # 실패 이유. 사용자에게 보여줄 한국어 문장이다.
    error: Mapped[str | None] = mapped_column(Text)

    # 만든 시각 · 끝난 시각 · 파일을 지운 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Flag(Base):
    """설치한 DB에서 한 번만 하는 일을 했다는 표시. 줄이 있으면 다시 하지 않는다.

    예: 처음 실행할 때 내장 예시 넣기. 작업 기록은 90일 뒤 지우므로 '한 번만'을 지키려고 따로 둔다.
    """

    __tablename__ = "system_flags"

    # 표시 이름 (예: first_run_examples). 일마다 하나라서 이것이 번호다.
    key: Mapped[str] = mapped_column(Text, primary_key=True)

    # 한 시각
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
