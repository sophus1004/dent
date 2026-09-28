"""가져오기 기록(system_imports): 어느 데이터셋에 어디서 가져왔고, 몇 줄을 넣고 건너뛰었는지.

모든 모듈이 같은 기록을 쓴다. 가져오기 한 번은 작업 대기열의 작업(종류 import) 하나로 돈다.
줄을 어떻게 넣고 무엇을 건너뛸지는 모듈이 정하고, 모듈만의 결과(예: 분류의 새 라벨)는 result에 담는다.

가져오기가 끝나면(finish_import · fail_import) 올린 원본 파일을 지운다. 줄은 이미 DB에 있고,
실패했으면 사용자가 파일을 다시 올린다. 중간에 끊겨 다시 대기로 돌아간 동안에는 지우지 않는다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import datasets, jobs, uploads
from dent.system.exceptions import NotFoundError
from dent.system.models import Import, ImportSource, ImportStatus

# 가져오기 작업의 종류. 작업 실행기는 (모듈 이름, IMPORT_JOB_KIND)로 모듈의 처리 함수를 찾는다.
IMPORT_JOB_KIND = "import"

# 건너뛴 줄 가운데 기록으로 남기는 수. 나머지는 수만 센다.
SKIPPED_LINES_KEPT = 100


async def create_import(
    db: AsyncSession,
    *,
    module: str,
    dataset_id: int,
    source: ImportSource,
    source_name: str,
    options: dict[str, Any],
) -> Import:
    """가져오기 기록을 만들고 작업을 대기열에 넣는다.

    커밋은 부른 쪽이 한다(새 데이터셋과 기록과 작업을 한 번에 저장하려고).
    """
    import_row = Import(
        dataset_id=dataset_id, source=source, source_name=source_name, options=options
    )
    db.add(import_row)
    await db.flush()
    job = await jobs.enqueue_job(
        db,
        module=module,
        kind=IMPORT_JOB_KIND,
        params={"import_id": import_row.id},
        dataset_id=dataset_id,
    )
    import_row.job_id = job.id
    return import_row


async def get_import(db: AsyncSession, *, import_id: int) -> Import:
    """가져오기 기록 하나. 없으면 NotFoundError."""
    query = select(Import).where(Import.id == import_id).execution_options(populate_existing=True)
    import_row = await db.scalar(query)
    if import_row is None:
        raise NotFoundError("가져오기 기록을 찾을 수 없습니다.")
    return import_row


async def list_imports(db: AsyncSession, *, dataset_id: int) -> list[Import]:
    """데이터셋의 가져오기 기록을 최근 것부터 돌려준다. 데이터셋이 없으면 NotFoundError."""
    await datasets.get_dataset(db, dataset_id=dataset_id)
    query = (
        select(Import)
        .where(Import.dataset_id == dataset_id)
        .order_by(Import.created_at.desc(), Import.id.desc())
    )
    return list((await db.scalars(query)).all())


def start_import(import_row: Import) -> None:
    """가져오기를 처음부터 다시 도는 상태로 둔다. 커밋은 부른 쪽이 한다.

    다시 돌려도 결과가 같게 수와 건너뛴 줄을 비운다. 모듈만의 결과(result)는 남긴다.
    """
    import_row.status = ImportStatus.RUNNING
    import_row.rows_total = None
    import_row.rows_added = 0
    import_row.rows_skipped = 0
    import_row.skipped_lines = []
    import_row.error = None
    import_row.finished_at = None


def add_batch(import_row: Import, *, added: int, skipped: list[dict[str, Any]]) -> None:
    """한 묶음의 결과를 더한다. skipped는 [{line, reason}]이다. 커밋은 부른 쪽이 한다."""
    import_row.rows_added += added
    import_row.rows_skipped += len(skipped)
    # JSONB 칼럼은 안의 값을 고쳐도 SQLAlchemy가 모르므로 새 목록으로 바꿔 넣는다.
    import_row.skipped_lines = [*import_row.skipped_lines, *skipped][:SKIPPED_LINES_KEPT]


async def finish_import(db: AsyncSession, import_row: Import, *, job_id: int) -> None:
    """가져오기와 작업을 성공으로 끝내고, 올린 원본 파일을 지우고, 데이터셋의 수정 시각을 적는다.

    커밋은 부른 쪽이 한다.
    """
    import_row.status = ImportStatus.DONE
    import_row.finished_at = datetime.now(UTC)
    await _discard_upload(db, import_row)
    await datasets.touch_dataset(db, dataset_id=import_row.dataset_id)
    await jobs.finish_job(
        db,
        job_id=job_id,
        result={
            "import_id": import_row.id,
            "rows_added": import_row.rows_added,
            "rows_skipped": import_row.rows_skipped,
        },
    )


async def fail_import(db: AsyncSession, *, import_id: int, job_id: int, message: str) -> None:
    """가져오기와 작업을 실패로 끝내고 올린 원본 파일을 지운다. message는 사용자에게 보여줄 문장이다."""
    await db.execute(
        update(Import)
        .where(Import.id == import_id)
        .values(status=ImportStatus.FAILED, error=message, finished_at=datetime.now(UTC))
    )
    import_row = await db.get(Import, import_id)
    if import_row is not None:
        await _discard_upload(db, import_row)
    await jobs.fail_job(db, job_id=job_id, message=message)


async def _discard_upload(db: AsyncSession, import_row: Import) -> None:
    """끝난 가져오기가 읽은 올린 원본 파일을 지운다. 커밋은 부른 쪽이 한다.

    허깅페이스에서 내려받은 파일은 읽고 나면 sources.py가 바로 지우므로 올린 파일만 본다.
    """
    if import_row.source == ImportSource.FILE:
        await uploads.discard_upload(db, upload_id=import_row.options["upload_id"])
