"""내보낸 파일(system_exports): 기록 만들기 · 작업 넣기 · 파일 자리 · 끝내기 · 내려받기 · 정리.

어떤 모양으로 무엇을 담을지는 모듈이 정한다(모듈의 작업이 exports.file_path에 파일을 쓴다).
시스템은 모든 모듈이 같은 것만 한다: 기록과 작업, 보관 자리(storage/exports/{번호}/{파일 이름}),
끝내기 · 실패, 내려받을 파일 찾기, 보관 기간이 지난 파일 지우기.
"""

import contextlib
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import jobs
from dent.system.config import get_settings
from dent.system.exceptions import NotFoundError
from dent.system.models import Export, ExportStatus

# 내보내기 작업의 종류. 작업 실행기는 (모듈 이름, EXPORT_JOB_KIND)로 모듈의 처리 함수를 찾는다.
EXPORT_JOB_KIND = "export"

# storage 아래에서 내보낸 파일을 두는 폴더 이름
EXPORTS_DIR_NAME = "exports"

# 내보낸 파일을 두는 기간. 지나면 정리 작업이 파일을 지운다(기록은 남긴다).
EXPORT_RETENTION = timedelta(days=7)

# 데이터셋 하나의 내보내기 목록에 보일 최대 수 (최근 것부터)
MAX_LIST_SIZE = 50

NOT_FOUND_MESSAGE = "내보내기 기록을 찾을 수 없습니다."
FILE_GONE_MESSAGE = "내보낸 파일이 없습니다. 보관 기간이 지났거나 아직 만드는 중입니다."

# 파일 이름에 쓰지 않을 글자
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


async def create_export(
    db: AsyncSession,
    *,
    module: str,
    dataset_id: int,
    format: str,
    file_name: str,
    options: dict[str, Any],
) -> Export:
    """내보내기 기록을 만들고 작업을 대기열에 넣는다. 커밋은 부른 쪽이 한다."""
    export = Export(
        module=module,
        dataset_id=dataset_id,
        format=format,
        file_name=safe_file_name(file_name),
        options=options,
    )
    db.add(export)
    await db.flush()
    job = await jobs.enqueue_job(
        db,
        module=module,
        kind=EXPORT_JOB_KIND,
        params={"export_id": export.id},
        dataset_id=dataset_id,
    )
    export.job_id = job.id
    return export


async def get_export(db: AsyncSession, *, export_id: int) -> Export:
    """내보내기 기록 하나. 없으면 NotFoundError."""
    export = await db.get(Export, export_id, populate_existing=True)
    if export is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return export


async def list_exports(db: AsyncSession, *, dataset_id: int) -> list[Export]:
    """데이터셋의 내보내기 기록을 최근 것부터 (최대 MAX_LIST_SIZE)."""
    query = (
        select(Export)
        .where(Export.dataset_id == dataset_id)
        .order_by(Export.created_at.desc(), Export.id.desc())
        .limit(MAX_LIST_SIZE)
    )
    return list((await db.scalars(query)).all())


def exports_root() -> Path:
    """내보낸 파일을 두는 폴더 (storage/exports)."""
    return get_settings().storage_path / EXPORTS_DIR_NAME


def file_path(export: Export) -> Path:
    """내보낼 파일의 자리. 폴더가 없으면 만든다. 모듈의 작업이 여기에 쓴다."""
    folder = exports_root() / str(export.id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / export.file_name


def start_export(export: Export) -> None:
    """내보내기를 처음부터 다시 만드는 상태로 둔다. 커밋은 부른 쪽이 한다."""
    export.status = ExportStatus.RUNNING
    export.error = None
    export.size_bytes = None
    export.finished_at = None


async def finish_export(
    db: AsyncSession, export: Export, *, job_id: int, result: dict[str, Any]
) -> None:
    """내보내기와 작업을 성공으로 끝낸다(파일 크기를 적는다). 커밋은 부른 쪽이 한다."""
    path = file_path(export)
    export.status = ExportStatus.DONE
    export.size_bytes = path.stat().st_size if path.exists() else 0
    export.result = result
    export.finished_at = datetime.now(UTC)
    await jobs.finish_job(db, job_id=job_id, result={"export_id": export.id, **result})


async def fail_export(db: AsyncSession, *, export_id: int, job_id: int, message: str) -> None:
    """내보내기와 작업을 실패로 끝내고 반쯤 쓴 파일을 지운다. message는 사용자에게 보여줄 문장이다."""
    await db.execute(
        update(Export)
        .where(Export.id == export_id)
        .values(status=ExportStatus.FAILED, error=message, finished_at=datetime.now(UTC))
    )
    _remove_folder(export_id)
    await jobs.fail_job(db, job_id=job_id, message=message)


async def find_file(db: AsyncSession, *, export_id: int) -> tuple[Export, Path]:
    """내려받을 파일. 기록이 없으면 NotFoundError, 파일이 없거나(만드는 중 · 지움) 끝나지 않았으면 NotFoundError."""
    export = await get_export(db, export_id=export_id)
    path = exports_root() / str(export.id) / export.file_name
    is_ready = export.status == ExportStatus.DONE and export.deleted_at is None and path.exists()
    if not is_ready:
        raise NotFoundError(FILE_GONE_MESSAGE)
    return export, path


async def discard_expired_exports(db: AsyncSession) -> int:
    """보관 기간이 지난 내보낸 파일을 지우고 지운 시각을 적는다. 지운 수를 돌려준다. 커밋은 부른 쪽이 한다."""
    cutoff = datetime.now(UTC) - EXPORT_RETENTION
    query = select(Export).where(Export.deleted_at.is_(None), Export.created_at < cutoff)
    expired = list((await db.scalars(query)).all())
    now = datetime.now(UTC)
    for export in expired:
        _remove_folder(export.id)
        export.deleted_at = now
    return len(expired)


def safe_file_name(file_name: str) -> str:
    """파일 이름에서 경로 · 조절 글자를 뺀다. 비면 'export'."""
    cleaned = _UNSAFE_NAME.sub("-", file_name).strip(" .-")
    return cleaned or "export"


def _remove_folder(export_id: int) -> None:
    """내보내기 폴더(storage/exports/{번호})를 통째로 지운다. 없으면 그냥 둔다."""
    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(exports_root() / str(export_id))
