"""작업 기록 화면을 위한 작업(system_jobs) 칸 셋: dataset_id · dataset_name · events.

- dataset_id: 작업이 다루는 데이터셋. 데이터셋을 지워도 작업 기록은 남기고 번호만 비운다(SET NULL).
  작업 기록 화면이 데이터셋별로 거르려고 인덱스를 건다.
- dataset_name: 넣을 때의 데이터셋 이름. 데이터셋을 지운 뒤에도 기록에 이름이 보이게 적어 둔다.
- events: 시작 · 단계 · 다시 보내기 · 쓴 연결을 시각 순으로 쌓는 목록. result와 달리 덮어쓰지 않는다.

있던 작업은 가져오기 기록(system_imports.job_id)과 의미 지도(classification_maps.job_id)에서 데이터셋을 찾아 채운다.
앞서 실패해 지운 지도의 작업처럼 찾을 수 없는 것은 비워 둔다. events는 빈 목록으로 시작한다.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25 14:29:51.921546
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """작업에 데이터셋 · 이름 · 사건 목록 칸을 더하고, 있던 작업의 데이터셋을 채운다."""
    op.add_column("system_jobs", sa.Column("dataset_id", sa.BigInteger(), nullable=True))
    op.add_column("system_jobs", sa.Column("dataset_name", sa.String(length=200), nullable=True))
    op.add_column(
        "system_jobs",
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_system_jobs_dataset_id"), "system_jobs", ["dataset_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_system_jobs_dataset_id_system_datasets"),
        "system_jobs",
        "system_datasets",
        ["dataset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # 있던 작업: 가져오기 기록 · 의미 지도가 가리키는 작업에 그 데이터셋과 지금 이름을 적는다.
    for owner in ("system_imports", "classification_maps"):
        op.execute(
            f"""
            UPDATE system_jobs AS job
            SET dataset_id = owner.dataset_id, dataset_name = dataset.name
            FROM {owner} AS owner
            JOIN system_datasets AS dataset ON dataset.id = owner.dataset_id
            WHERE owner.job_id = job.id AND job.dataset_id IS NULL
            """
        )


def downgrade() -> None:
    """더한 칸을 뺀다."""
    op.drop_constraint(
        op.f("fk_system_jobs_dataset_id_system_datasets"), "system_jobs", type_="foreignkey"
    )
    op.drop_index(op.f("ix_system_jobs_dataset_id"), table_name="system_jobs")
    op.drop_column("system_jobs", "events")
    op.drop_column("system_jobs", "dataset_name")
    op.drop_column("system_jobs", "dataset_id")
