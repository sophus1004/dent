"""올린 파일(csv·tsv·xlsx) 저장, 미리 보기, 지우기.

올린 파일은 storage/uploads/YYYY/MM/{upload_id}/{파일 이름}에 두고, system_files에 한 줄을 적는다
(원래 이름, storage 기준 경로, 크기, sha256). sha256은 나중에 같은 파일을 다시 올렸는지 알아볼 때 쓴다.
표를 읽는 규칙은 tables.py에 있다. 미리 보기와 가져오기가 같은 읽기 함수를 쓴다.

원본 파일은 가져오기에만 쓴다. 가져오기가 끝나면(성공·실패) imports.py가 discard_upload로 지우고,
가져오지 않은 채 오래된 파일은 정리 작업(jobs.run_cleanup)이 지운다.
목록의 한 줄은 남기고 지운 시각(deleted_at)만 적는다.
"""

import asyncio
import contextlib
import hashlib
import itertools
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import tables
from dent.system.config import get_settings
from dent.system.exceptions import AppError, InvalidInputError, NotFoundError
from dent.system.models import File, Import, ImportSource, ImportStatus
from dent.system.schemas import FilePreviewRead

UPLOAD_NOT_FOUND_MESSAGE = "올린 파일을 찾을 수 없습니다. 파일을 다시 올려 주세요."
UPLOAD_DELETED_MESSAGE = "올린 파일이 지워졌습니다. 다시 올려 주세요."

# storage 아래에서 올린 파일을 두는 폴더 이름
UPLOADS_DIR_NAME = "uploads"

# 미리 보기로 보여 줄 줄 수
PREVIEW_ROWS = 20

# 올린 파일 번호 모양: uuid4의 16진수 32자. 이 모양이 아니면 찾아보지 않는다.
UPLOAD_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

# 저장하는 파일 이름에 남길 글자: 글자·숫자(한글 포함), 점, 빼기. 나머지는 _로 바꾼다.
UNSAFE_FILE_NAME_CHARS = re.compile(r"[^\w.\-]+")

# 저장하는 파일 이름의 최대 길이. 대부분의 파일 시스템이 255바이트까지 받는다(한글은 3바이트).
FILE_NAME_MAX_LENGTH = 80

# 가져오지 않은 올린 파일을 남겨 두는 기간. 올리고 가져오기를 시작하기까지 넉넉한 시간이다.
# 이보다 오래되면 정리 작업이 지운다.
UNUSED_UPLOAD_RETENTION = timedelta(days=1)

# 파일을 쓰고 있는 가져오기의 상태. 이 상태의 가져오기가 쓰는 파일은 정리 작업이 지우지 않는다.
IN_USE_IMPORT_STATUSES = (ImportStatus.QUEUED, ImportStatus.RUNNING)


def uploads_root() -> Path:
    """올린 파일을 두는 폴더."""
    return get_settings().storage_path / UPLOADS_DIR_NAME


async def save_upload(
    db: AsyncSession, *, file_name: str, read: Callable[[int], Awaitable[bytes]]
) -> FilePreviewRead:
    """올린 파일을 조각조각 저장하고, 목록에 적고, 미리 보기를 돌려준다. 형식이 틀리면 InvalidInputError."""
    safe_name = _safe_file_name(file_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in tables.FORMAT_BY_SUFFIX:
        raise InvalidInputError(tables.UNSUPPORTED_FORMAT_MESSAGE)

    upload_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    folder = uploads_root() / f"{now:%Y}" / f"{now:%m}" / upload_id
    folder.mkdir(parents=True)
    path = folder / safe_name
    try:
        size_bytes, sha256 = await _write_chunks(path, read=read)
        # 큰 파일은 줄 수를 세는 데 몇 초가 걸리므로, 그동안 서버가 다른 요청을 받게 따로 돌린다.
        preview = await asyncio.to_thread(_build_preview, upload_id, path, None)
    except AppError:
        # 읽을 수 없는 파일은 남겨 두지 않는다.
        shutil.rmtree(folder, ignore_errors=True)
        raise

    stored_path = path.relative_to(get_settings().storage_path).as_posix()
    db.add(
        File(
            id=upload_id,
            original_name=file_name,
            stored_path=stored_path,
            size_bytes=size_bytes,
            sha256=sha256,
        )
    )
    await db.commit()
    return preview


async def get_file(db: AsyncSession, *, upload_id: str) -> File:
    """올린 파일 한 건의 기록. 없으면 NotFoundError."""
    is_valid_id = UPLOAD_ID_PATTERN.fullmatch(upload_id) is not None
    file = await db.get(File, upload_id) if is_valid_id else None
    if file is None:
        raise NotFoundError(UPLOAD_NOT_FOUND_MESSAGE)
    return file


async def find_upload(db: AsyncSession, *, upload_id: str) -> Path:
    """upload_id로 저장한 파일의 경로를 찾는다. 기록이나 파일이 없거나 지웠으면 NotFoundError."""
    file = await get_file(db, upload_id=upload_id)
    if file.deleted_at is not None:
        raise NotFoundError(UPLOAD_DELETED_MESSAGE)
    path = get_settings().storage_path / file.stored_path
    if not path.is_file():
        raise NotFoundError(UPLOAD_NOT_FOUND_MESSAGE)
    return path


async def preview_upload(db: AsyncSession, *, upload_id: str, sheet: str | None) -> FilePreviewRead:
    """올린 파일의 미리 보기. 파일이 없으면 NotFoundError, 시트가 없으면 NotFoundError."""
    path = await find_upload(db, upload_id=upload_id)
    return await asyncio.to_thread(_build_preview, upload_id, path, sheet)


async def discard_upload(db: AsyncSession, *, upload_id: str) -> None:
    """올린 원본 파일을 지우고 목록에 지운 시각을 적는다. 기록이 없거나 이미 지웠으면 그냥 둔다.

    목록의 한 줄(이름·크기·해시)은 어디서 가져왔는지 알려고 남긴다. 커밋은 부른 쪽이 한다.
    """
    file = await db.get(File, upload_id)
    if file is None or file.deleted_at is not None:
        return
    _discard(file)


async def discard_unused_uploads(db: AsyncSession) -> int:
    """가져오지 않은 채 UNUSED_UPLOAD_RETENTION이 지난 올린 파일을 지운다. 지운 수를 돌려준다.

    대기·실행 중인 가져오기가 쓰는 파일은 남긴다. 커밋은 부른 쪽이 한다.
    """
    cutoff = datetime.now(UTC) - UNUSED_UPLOAD_RETENTION
    is_in_use = exists().where(
        Import.source == ImportSource.FILE,
        Import.status.in_(IN_USE_IMPORT_STATUSES),
        Import.options["upload_id"].astext == File.id,
    )
    query = select(File).where(File.deleted_at.is_(None), File.created_at < cutoff, ~is_in_use)
    files = list((await db.scalars(query)).all())
    for file in files:
        _discard(file)
    return len(files)


def _discard(file: File) -> None:
    """원본 파일과 빈 저장 폴더(uploads/YYYY/MM/{upload_id})를 지우고 지운 시각을 적는다."""
    path = get_settings().storage_path / file.stored_path
    # 이미 없으면(누가 손으로 지웠으면) 지운 것으로 적기만 한다.
    path.unlink(missing_ok=True)
    # rmdir은 빈 폴더만 지우므로 다른 파일을 함께 지울 걱정이 없다.
    with contextlib.suppress(OSError):
        path.parent.rmdir()
    file.deleted_at = datetime.now(UTC)


async def _write_chunks(path: Path, *, read: Callable[[int], Awaitable[bytes]]) -> tuple[int, str]:
    """올린 내용을 조각조각 쓰면서 크기와 sha256을 센다. (크기, sha256 16진수)를 돌려준다."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("wb") as target:
        while chunk := await read(tables.CHUNK_BYTES):
            target.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
    return size_bytes, digest.hexdigest()


def _build_preview(upload_id: str, path: Path, sheet: str | None) -> FilePreviewRead:
    """표를 열어 앞쪽 몇 줄을 담는다."""
    table = tables.open_table(path, sheet=sheet)
    first_rows = list(itertools.islice(table.rows, PREVIEW_ROWS))
    return FilePreviewRead(
        upload_id=upload_id,
        file_name=path.name,
        format=table.format,
        size_bytes=path.stat().st_size,
        encoding=table.encoding,
        delimiter=table.delimiter,
        sheets=table.sheets,
        sheet=table.sheet,
        columns=table.columns,
        rows=[values for _line, values in first_rows],
        lines=[line for line, _values in first_rows],
        row_count=table.row_count,
    )


def _safe_file_name(file_name: str) -> str:
    """경로 조각과 이상한 글자를 뺀 파일 이름. 확장자는 지킨다."""
    base = Path(file_name.replace("\\", "/")).name
    cleaned = UNSAFE_FILE_NAME_CHARS.sub("_", base).lstrip(".")
    stem, suffix = Path(cleaned).stem, Path(cleaned).suffix
    stem = stem[: FILE_NAME_MAX_LENGTH - len(suffix)] or "upload"
    return f"{stem}{suffix}"
