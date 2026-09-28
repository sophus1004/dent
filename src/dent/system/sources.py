"""원본 열기: 올린 파일이나 허깅페이스 데이터셋을 줄(줄 번호, {열 이름: 값})로 읽는다.

시스템은 "원본 → 줄"까지만 한다. 줄을 자기 데이터로 바꾸는 일(필드 맞춤, 건너뛸 줄 고르기)은
모듈이 한다. 모듈의 가져오기는 open_source로 원본을 열고 rows를 돌며 넣는다.

- 파일: 줄 번호는 csv면 파일의 줄(칸 이름 줄이 1), 엑셀이면 시트의 행 번호다.
- 허깅페이스: 고른 분할의 Parquet 파일을 storage/uploads/hf/{가져오기 번호}/에 내려받아 차례로 읽는다.
  줄 번호는 이번 가져오기에서 1부터 센 순서이고, 줄마다 분할 이름이 붙는다.
  내려받은 파일은 이번 가져오기에만 쓰고 지운다. 원본은 허깅페이스에 있다.
"""

import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import huggingface, tables, uploads
from dent.system.exceptions import InvalidInputError, NotFoundError
from dent.system.models import Import, ImportSource
from dent.system.schemas import CellValue, SourceCreate

# 허깅페이스 파일을 내려받는 폴더 이름 (storage/uploads 아래)
HF_DOWNLOAD_DIR_NAME = "hf"


@dataclass(frozen=True)
class SourceRow:
    """원본의 한 줄."""

    # 건너뛴 줄을 알려 줄 때 쓰는 줄 번호
    line: int

    # {열 이름: 값}
    values: dict[str, CellValue]

    # 허깅페이스 분할 이름. 파일이면 None.
    split_name: str | None


@dataclass
class OpenedSource:
    """읽을 준비가 된 원본."""

    # 열 이름들
    columns: list[str]

    # 전체 줄 수
    total_rows: int

    # 줄들 (한 번만 읽을 수 있다)
    rows: Iterator[SourceRow]


async def describe_source(db: AsyncSession, *, data: SourceCreate) -> tuple[str, dict[str, Any]]:
    """가져올 곳을 확인하고 (화면에 보일 이름, 가져오기 설정)을 돌려준다.

    값이 모자라면 InvalidInputError, 올린 파일이 없으면 NotFoundError.
    """
    if data.source == ImportSource.FILE:
        if not data.upload_id:
            raise InvalidInputError("올린 파일 번호(upload_id)가 필요합니다.")
        path = await uploads.find_upload(db, upload_id=data.upload_id)
        return path.name, {"upload_id": data.upload_id, "sheet": data.sheet}

    if not data.repo:
        raise InvalidInputError("허깅페이스 저장소 이름(repo)이 필요합니다.")
    source_name = f"{data.repo} · {data.config}" if data.config else data.repo
    return source_name, {"repo": data.repo, "config": data.config, "splits": data.splits}


@asynccontextmanager
async def open_source(
    db: AsyncSession, import_row: Import, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[OpenedSource]:
    """가져오기 기록의 설정대로 원본을 연다. 읽을 수 없으면 도메인 예외를 낸다.

    transport는 테스트가 허깅페이스 대신 가짜 응답을 줄 때만 쓴다.
    """
    options = import_row.options
    if import_row.source == ImportSource.FILE:
        path = await uploads.find_upload(db, upload_id=options["upload_id"])
        table = tables.open_table(path, sheet=options.get("sheet"))
        rows = (SourceRow(line=line, values=values, split_name=None) for line, values in table.rows)
        yield OpenedSource(columns=table.columns, total_rows=table.row_count, rows=rows)
        return

    download_dir = uploads.uploads_root() / HF_DOWNLOAD_DIR_NAME / str(import_row.id)
    try:
        yield await _open_huggingface(options, download_dir=download_dir, transport=transport)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


async def _open_huggingface(
    options: dict[str, Any], *, download_dir: Path, transport: httpx.AsyncBaseTransport | None
) -> OpenedSource:
    """허깅페이스 분할들의 Parquet 파일을 내려받고, 차례로 읽을 준비를 한다."""
    repo = options["repo"]
    config = options.get("config") or await huggingface.first_config(repo=repo, transport=transport)
    parquet = await huggingface.list_parquet_files(repo=repo, config=config, transport=transport)
    urls_by_split = parquet.urls_by_split
    wanted_splits = options.get("splits") or list(urls_by_split)
    for split_name in wanted_splits:
        if split_name not in urls_by_split:
            raise NotFoundError(f"허깅페이스 데이터셋 {repo}에 '{split_name}' 분할이 없습니다.")
        # 앞부분만 받아 두고 '다 가져왔다'고 하면 사용자는 줄이 빠진 것을 모른다. 미리 멈춘다.
        if split_name in parquet.partial_splits:
            raise InvalidInputError(
                f"허깅페이스가 '{split_name}' 분할을 앞부분만 변환해 두어서 "
                "전부 가져올 수 없습니다. "
                "너무 큰 분할입니다. 다른 분할이나 구성을 고르세요."
            )
    label_names = await huggingface.fetch_label_names(repo=repo, config=config, transport=transport)

    downloaded: list[tuple[str, Path]] = []
    for split_index, split_name in enumerate(wanted_splits):
        for file_index, url in enumerate(urls_by_split[split_name]):
            # 분할 이름에 어떤 글자가 올지 몰라 파일 이름은 번호로 짓는다.
            target = download_dir / f"{split_index}-{file_index}.parquet"
            await huggingface.download(url=url, target=target, repo=repo, transport=transport)
            downloaded.append((split_name, target))

    columns = tables.parquet_columns(downloaded[0][1]) if downloaded else []
    total_rows = sum(tables.parquet_row_count(path) for _split, path in downloaded)
    rows = _huggingface_rows(downloaded, label_names=label_names)
    return OpenedSource(columns=columns, total_rows=total_rows, rows=rows)


def _huggingface_rows(
    downloaded: list[tuple[str, Path]], *, label_names: dict[str, list[str]]
) -> Iterator[SourceRow]:
    """내려받은 파일들을 차례로 읽는다. 줄 번호는 이번 가져오기에서 1부터 센 순서다."""
    line = 0
    for split_name, path in downloaded:
        for values in huggingface.read_rows(path, label_names=label_names):
            line += 1
            yield SourceRow(line=line, values=values, split_name=split_name)
