"""분류 모듈 테이블 (classification_): 라벨과 문장, 의미 지도 · 뜻 분석, 도우미의 변경 기록.

데이터셋(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이고, 분류 테이블이 참조한다.
분류 테이블은 시스템 테이블만 참조하고, 다른 모듈의 테이블은 모른다.
뺀 이유 · 지도 상태 같은 고정된 값은 text + CHECK 제약으로 저장한다.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dent.system.db import Base, check_in
from dent.system.models import EMPTY_JSON_OBJECT, EmbeddingModel
from dent.system.text import TEXT_HASH_LENGTH

# 라벨 이름의 최대 길이
LABEL_NAME_MAX_LENGTH = 200

# 문장 한 건의 최대 글자 수. 이보다 긴 문장은 가져오지 않는다(분류 학습 문장으로 보기 어렵다).
TEXT_MAX_LENGTH = 10_000

# 뺀 이유·지도 상태 값의 최대 길이
EXCLUDE_REASON_MAX_LENGTH = 16
MAP_STATUS_MAX_LENGTH = 16

# 도우미 실행 설정의 기본값: 새 문장(라벨 균형)의 목표 배율. 가장 많은 라벨 ÷ 이 배율까지 모자란 라벨을 채운다
# (진단의 라벨 균형 '좋음' 경계와 같다).
DEFAULT_BALANCE_TARGET = 1.5


class ExcludeReason(StrEnum):
    """학습에서 뺀 이유. 비어 있으면(None) 학습에 쓴다."""

    # 사람이 직접 뺐다
    MANUAL = "manual"

    # 중복 정리로 빠졌다. 정리 되돌리기는 이 값만 되돌린다.
    DUPLICATE = "duplicate"

    # LLM 도우미가 뺐다. 도우미의 변경 기록으로 되돌린다.
    HELPER = "helper"


class MapStatus(StrEnum):
    """의미 지도 만들기의 상태."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# 만드는 중으로 보는 지도 상태. 데이터셋마다 한 번에 하나만 돈다.
MAP_BUILDING_STATUSES = (MapStatus.QUEUED, MapStatus.RUNNING)


class SuspectDecision(StrEnum):
    """오라벨 의심을 사람이 어떻게 했는지. 아직 안 했으면 비워 둔다(대기)."""

    # 추천(또는 고른) 라벨로 바꿨다
    ACCEPTED = "accepted"
    # 지금 라벨이 맞다. 다음 뜻 분석에서도 다시 묻지 않는다.
    KEPT = "kept"


# 판단 칸의 최대 길이
SUSPECT_DECISION_MAX_LENGTH = 16


class Label(Base):
    """라벨 하나. 데이터셋 안에서 이름이 겹치지 않는다."""

    # 이 모델이 저장되는 테이블 이름. 분류 모듈 테이블은 classification_으로 시작한다.
    __tablename__ = "classification_labels"
    __table_args__ = (UniqueConstraint("dataset_id", "name"),)

    # 라벨 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 라벨도 함께 지운다. 데이터셋별로 라벨을 찾는 일이 잦다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 라벨 이름
    name: Mapped[str] = mapped_column(String(LABEL_NAME_MAX_LENGTH))

    # 라벨 기준 설명. 없으면 빈 문자열.
    description: Mapped[str] = mapped_column(Text, server_default="")

    # 만든 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Record(Base):
    """문장 한 건: 원문, 라벨, 학습 제외 이유, 원본의 남는 필드. train · valid · test로 나누지 않는다(한 덩어리)."""

    __tablename__ = "classification_records"
    __table_args__ = (
        CheckConstraint(check_in("exclude_reason", ExcludeReason), name="exclude_reason"),
        # 같은 문장 찾기(중복·충돌)는 데이터셋 안에서 해시로 묶는다. 라벨·상태까지
        # 인덱스에 담아 두면 DB가 표를 읽지 않고 인덱스만 해시 순서대로 훑어 센다.
        # 수백만 건에서 진단과 문제 목록이 몇 배 빨라진다(표를 해시 순서로 뒤져 읽지 않으므로).
        Index(
            "ix_classification_records_same_text",
            "dataset_id",
            "text_hash",
            "label_id",
            postgresql_include=["trashed_at", "exclude_reason"],
        ),
        # 라벨별 목록과 개수
        Index("ix_classification_records_dataset_id_label_id", "dataset_id", "label_id"),
        # 가져오기를 다시 돌릴 때 그 가져오기가 넣은 줄을 지운다.
        Index("ix_classification_records_dataset_id_import_id", "dataset_id", "import_id"),
    )

    # 문장 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 문장도 함께 지운다.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("system_datasets.id", ondelete="CASCADE"))

    # 이 문장을 넣은 가져오기 번호. 직접 넣었거나 가져오기 기록이 지워졌으면 비어 있다.
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_imports.id", ondelete="SET NULL")
    )

    # 라벨 번호. 라벨이 지워지면 비운다.
    label_id: Mapped[int | None] = mapped_column(
        ForeignKey("classification_labels.id", ondelete="SET NULL")
    )

    # 문장 원문 (앞뒤 공백을 뺀 것)
    text: Mapped[str] = mapped_column(Text)

    # 같은 문장을 찾는 열쇠. 유니코드 정규화(NFC)하고 공백을 하나로 줄인 문장의 sha256.
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH))

    # 학습에서 뺀 이유: manual · duplicate. 비어 있으면 학습에 쓴다.
    exclude_reason: Mapped[str | None] = mapped_column(String(EXCLUDE_REASON_MAX_LENGTH))

    # 휴지통에 넣은 시각. 비어 있으면 휴지통에 없다.
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 고칠 때마다 1씩 오르는 번호. 다른 창에서 먼저 고쳤는지 알아보는 데 쓴다.
    row_version: Mapped[int] = mapped_column(Integer, server_default="1")

    # 원본에서 문장·라벨 말고 남는 필드들 {열 이름: 값}
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 만든 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 마지막으로 고친 시각
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 라벨 정보(Label).
    # lazy="raise": 미리 불러오지 않은 라벨에 접근하면 바로 에러를 내서 N+1을 막는다.
    label: Mapped[Label | None] = relationship(lazy="raise")


class Map(Base):
    """의미 지도 한 번 만들기: 데이터셋의 문장을 임베딩해 2차원 좌표로 줄인 것.

    데이터셋마다 다 만든 지도는 가장 최근 것 하나만 남긴다(새 지도가 끝나면 앞의 것을 지운다).
    만드는 중 · 실패 · 취소한 시도도 한 줄로 남아 화면이 진행과 까닭을 보인다.
    """

    __tablename__ = "classification_maps"
    __table_args__ = (
        CheckConstraint(check_in("status", MapStatus), name="status"),
        # 데이터셋마다 만드는 중인 지도는 하나뿐이다. 두 창에서 동시에 눌러도 둘이 돌지 않게 DB가 막는다.
        Index(
            "uq_classification_maps_building",
            "dataset_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    # 지도 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 데이터셋 번호. 데이터셋을 지우면 지도도 지운다. 데이터셋별로 지도를 찾는다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), index=True
    )

    # 쓴 임베딩 모델 번호. 작업이 시작할 때 그때의 연결로 정한다(대기 중에는 비어 있다).
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("system_embedding_models.id", ondelete="SET NULL")
    )

    # 상태: queued · running · done · failed · canceled
    status: Mapped[str] = mapped_column(
        String(MAP_STATUS_MAX_LENGTH), server_default=MapStatus.QUEUED.value
    )

    # 좌표를 만든 방법과 값 (UMAP의 n_neighbors · min_dist · metric · random_state …)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 뜻 분석 결과 요약: 기준값, 근접 중복 · 오라벨 의심 수, 라벨마다 의미 쏠림(semantic_checks.py가 만든다).
    # 뜻 분석이 생기기 전의 지도는 비어 있다. 쌍 · 의심 한 건씩은 따로 된 표에 둔다.
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    # 지도에 놓은 서로 다른 문장 수 (같은 문장은 한 점 자리를 같이 쓴다)
    text_count: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 지도의 점 수 (문장 한 건이 점 하나)
    point_count: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 만들기 시작할 때 휴지통 밖 문장 수. 지금 수와 다르면 지도 이후 바뀐 것이다.
    record_count: Mapped[int] = mapped_column(BigInteger, server_default="0")

    # 만들기 시작할 때 데이터셋의 수정 시각. 지금 시각이 더 늦으면 지도 이후 바뀐 것이다.
    dataset_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 이 지도를 만드는 작업 번호. 오래된 작업 기록이 지워지면 비운다.
    job_id: Mapped[int | None] = mapped_column(ForeignKey("system_jobs.id", ondelete="SET NULL"))

    # 실패 이유. 사용자에게 보여줄 한국어 문장이다.
    error: Mapped[str | None] = mapped_column(Text)

    # 만들기를 누른 시각
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 끝난 시각 (다 만듦 · 실패 · 취소)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 쓴 임베딩 모델(EmbeddingModel). 미리 불러오지 않고 접근하면 바로 에러를 낸다(N+1 방지).
    model: Mapped[EmbeddingModel | None] = relationship(lazy="raise")


class MapPoint(Base):
    """의미 지도의 점 하나: 문장 한 건의 2차원 좌표. 같은 문장(같은 해시)이면 좌표도 같다."""

    __tablename__ = "classification_map_points"

    # 지도 번호. 지도를 지우면 점도 지운다. (지도, 문장)이 번호라서 지도별로 점을 읽는 인덱스가 저절로 생긴다.
    map_id: Mapped[int] = mapped_column(
        ForeignKey("classification_maps.id", ondelete="CASCADE"), primary_key=True
    )

    # 문장 번호. 문장을 지우면 점도 지운다.
    # 문장을 지울 때 DB가 이 칸으로 점을 찾으므로 인덱스를 건다(없으면 문장마다 점 표 전체를 훑는다).
    record_id: Mapped[int] = mapped_column(
        ForeignKey("classification_records.id", ondelete="CASCADE"), primary_key=True, index=True
    )

    # 가로 좌표 [-1, 1]
    x: Mapped[float] = mapped_column(REAL)

    # 세로 좌표 [-1, 1]
    y: Mapped[float] = mapped_column(REAL)


class NearDuplicate(Base):
    """근접 중복 한 쌍: 글자는 다르지만 뜻이 거의 같은 두 문장(뜻 분석 한 번의 결과).

    문장 해시 두 개를 작은 것 · 큰 것 순서로 한 번만 적는다. 뜻 분석(지도)을 지우면 함께 지운다.
    """

    __tablename__ = "classification_near_duplicates"
    __table_args__ = (
        # 한 문장이 쌍의 뒤쪽에 있을 때도 빨리 찾으려고(앞쪽은 기본 키로 찾는다).
        Index("ix_classification_near_duplicates_map_id_text_hash_b", "map_id", "text_hash_b"),
    )

    # 뜻 분석(지도) 번호
    map_id: Mapped[int] = mapped_column(
        ForeignKey("classification_maps.id", ondelete="CASCADE"), primary_key=True
    )

    # 앞 문장 해시 (둘 가운데 작은 것)
    text_hash_a: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH), primary_key=True)

    # 뒤 문장 해시 (둘 가운데 큰 것)
    text_hash_b: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH), primary_key=True)

    # 두 문장 임베딩의 코사인 유사도
    similarity: Mapped[float] = mapped_column(REAL)


class LabelSuspect(Base):
    """오라벨 의심 한 건: 뜻으로 보면 다른 라벨이 어울리는 문장(뜻 분석 한 번의 결과).

    같은 문장(해시)은 한 줄이다. 근거는 임베딩 분류기(교차 검증 확률)와, 있으면 Jev 판정이다.
    사람이 수락하면 그 문장의 라벨을 바꾸고, 유지하면 다음 뜻 분석에서도 다시 묻지 않는다.
    """

    __tablename__ = "classification_label_suspects"
    __table_args__ = (CheckConstraint(check_in("decision", SuspectDecision), name="decision"),)

    # 뜻 분석(지도) 번호
    map_id: Mapped[int] = mapped_column(
        ForeignKey("classification_maps.id", ondelete="CASCADE"), primary_key=True
    )

    # 문장 해시
    text_hash: Mapped[str] = mapped_column(String(TEXT_HASH_LENGTH), primary_key=True)

    # 지금 라벨
    label_id: Mapped[int] = mapped_column(
        ForeignKey("classification_labels.id", ondelete="CASCADE")
    )

    # 분류기가 추천한 라벨
    suggested_label_id: Mapped[int] = mapped_column(
        ForeignKey("classification_labels.id", ondelete="CASCADE")
    )

    # 분류기가 매긴 지금 라벨의 확률 (자기 자신은 빼고 학습한 모델)
    label_probability: Mapped[float] = mapped_column(REAL)

    # 분류기가 매긴 추천 라벨의 확률
    suggested_probability: Mapped[float] = mapped_column(REAL)

    # Jev가 고른 라벨. Jev에 묻지 않았거나(미연결 · 상한 넘음) 고른 이름을 못 찾으면 비운다.
    jev_label_id: Mapped[int | None] = mapped_column(
        ForeignKey("classification_labels.id", ondelete="SET NULL")
    )

    # Jev가 매긴 지금 라벨의 확률
    jev_label_probability: Mapped[float | None] = mapped_column(REAL)

    # Jev의 확신도
    jev_confidence: Mapped[float | None] = mapped_column(REAL)

    # Jev도 지금 라벨이 아니라고 봤는지(다른 라벨을 고르고 지금 라벨 확률이 기준 아래)
    is_confirmed: Mapped[bool] = mapped_column(server_default=text("false"))

    # 사람의 판단: accepted · kept. 아직이면 비운다.
    decision: Mapped[str | None] = mapped_column(String(SUSPECT_DECISION_MAX_LENGTH))

    # 판단한 시각
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetSettings(Base):
    """분류 데이터셋의 도우미 실행 설정. 줄이 없으면 기본값으로 본다(처음 고칠 때 만든다)."""

    __tablename__ = "classification_dataset_settings"

    # 데이터셋 번호. 데이터셋을 지우면 설정도 지운다.
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("system_datasets.id", ondelete="CASCADE"), primary_key=True
    )

    # 새 문장(라벨 균형)의 목표 배율. 화면이 고른 값(1.2 · 1.5 · 2)을 그대로 돌려주려고 배정밀도로 둔다.
    balance_target: Mapped[float] = mapped_column(
        Double, server_default=str(DEFAULT_BALANCE_TARGET)
    )

    # 고친 시각
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HelperChangeField(StrEnum):
    """도우미가 바꾼 칸. 되돌릴 때 이 칸을 전 값으로 돌린다."""

    # 라벨 (전 · 후 = 라벨 번호)
    LABEL = "label"
    # 학습 포함 · 제외 (전 · 후 = 뺀 이유, 포함이면 null)
    EXCLUDE = "exclude"
    # 도우미가 새로 만든 문장 (되돌리면 휴지통으로)
    CREATED = "created"


# 도우미 변경 칸 이름의 최대 길이
HELPER_CHANGE_FIELD_MAX_LENGTH = 16


class HelperChange(Base):
    """LLM 도우미가 문장 하나를 바꾼 기록. 사건(바꾼 카드) 하나에 여러 줄이 붙고, 카드 · 실행 단위로 되돌린다.

    되돌릴 때 지금 값이 after와 같을 때만 before로 돌린다(그 사이 사람이 고친 것은 건드리지 않는다).
    """

    __tablename__ = "classification_helper_changes"
    __table_args__ = (CheckConstraint(check_in("field", HelperChangeField), name="field"),)

    # 기록 번호
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    # 도우미 실행 번호. 실행 기록을 지우면 함께 지운다. 실행별로 되돌린다.
    run_id: Mapped[int] = mapped_column(
        ForeignKey("system_helper_runs.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 카드(사건) 번호. 카드 하나를 되돌릴 때 찾는다.
    event_id: Mapped[int] = mapped_column(
        ForeignKey("system_helper_events.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 문장. 문장을 영구히 지우면 기록도 지운다.
    record_id: Mapped[int] = mapped_column(
        ForeignKey("classification_records.id", ondelete="CASCADE"), index=True
    )

    # 바꾼 칸: label · exclude · created
    field: Mapped[str] = mapped_column(String(HELPER_CHANGE_FIELD_MAX_LENGTH))

    # 전 · 후 값 (라벨 번호 · 뺀 이유 · null)
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # 되돌린 시각. 아직이면 null
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
