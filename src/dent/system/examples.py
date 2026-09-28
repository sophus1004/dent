"""내장 예시 데이터의 바탕(모든 모듈): 설치하자마자 해 볼 수 있게 모듈이 저장소에 함께 둔 데이터셋.

모듈은 자기 폴더의 `example_data/`에 예시 파일(csv · xlsx)과 정답지(`<열쇠>.answers.json`, 일부러 심은 문제와 그 수)를 두고
예시 목록(Example)을 내놓는다. 넣기는 모듈이 보통 가져오기 길로 한다: 이 파일의 register_file로 예시 파일을 올린 파일처럼
등록한 뒤 모듈의 create_import(작업 → 가져오기 → 반복 구간 살피기 …). 그래서 예시도 진단 · 가져온 기록이 사람이 올린 것과 같다.
예시 파일은 `scripts/examples/build.py`가 원고(`scripts/examples/content/`)에 문제를 심어 만든다.
처음 실행할 때 작업 실행기가 모든 모듈의 예시를 한 번 넣는다(표시는 system_flags, 이 파일의 is_first_run_done ·
mark_first_run_done). 한 번 넣은 DB는 예시를 지워도 다시 넣지 않는다.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import uploads
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import Dataset, Flag
from dent.system.schemas import ExamplePlantedRead, ExampleRead

EXAMPLE_NOT_FOUND_MESSAGE = "예시 데이터를 찾을 수 없습니다."
EXAMPLE_INSTALLED_MESSAGE = "이미 넣은 예시입니다. 다시 넣으려면 그 데이터셋을 먼저 지우세요."

# 예시 파일을 올린 파일로 옮길 때 한 번에 읽는 크기 (올린 파일을 저장하는 것과 같은 조각 크기)
READ_CHUNK_BYTES = 1024 * 1024

# 처음 실행할 때 내장 예시를 넣었다(또는 쓰던 DB라 넣지 않기로 했다)는 표시 (system_flags의 key)
FIRST_RUN_FLAG = "first_run_examples"


@dataclass(frozen=True)
class Example:
    """내장 예시 데이터셋 하나."""

    # 예시 열쇠 (주소 · 정답지 파일 이름에 쓴다)
    key: str

    # 넣을 때 만드는 데이터셋 이름 (모듈 안에서 겹치지 않는다)
    name: str

    # 데이터셋 설명 (무엇을 보여 주는 예시인지)
    description: str

    # 모듈 example_data 폴더 안의 파일 이름 (csv · xlsx)
    file_name: str

    # 엑셀 시트 이름 (xlsx). 없으면 첫 시트.
    sheet: str | None = None


def read_answers(folder: Path, example: Example) -> dict[str, Any]:
    """예시의 정답지(일부러 심은 문제 · 수 · 줄 수). 없으면 빈 모양."""
    path = folder / f"{example.key}.answers.json"
    if not path.exists():
        return {"rows": 0, "planted": []}
    return json.loads(path.read_text(encoding="utf-8"))


def find_example(examples: tuple[Example, ...], *, key: str) -> Example:
    """열쇠로 예시를 찾는다. 없으면 NotFoundError."""
    found = next((example for example in examples if example.key == key), None)
    if found is None:
        raise NotFoundError(EXAMPLE_NOT_FOUND_MESSAGE)
    return found


async def ensure_not_installed(db: AsyncSession, *, module: str, example: Example) -> None:
    """같은 이름의 데이터셋이 이미 있으면 ConflictError (예시 파일을 등록하기 전에 본다)."""
    found = await db.scalar(
        select(Dataset.id).where(Dataset.module == module, Dataset.name == example.name)
    )
    if found is not None:
        raise ConflictError(EXAMPLE_INSTALLED_MESSAGE)


async def register_file(db: AsyncSession, *, folder: Path, example: Example) -> str:
    """예시 파일을 올린 파일로 등록한다(storage로 복사 · 목록에 적음). 올린 파일 번호."""
    path = folder / example.file_name
    if not path.exists():
        raise NotFoundError(EXAMPLE_NOT_FOUND_MESSAGE)
    with path.open("rb") as handle:

        async def read(size: int) -> bytes:
            return handle.read(min(size, READ_CHUNK_BYTES))

        preview = await uploads.save_upload(db, file_name=example.file_name, read=read)
    return preview.upload_id


async def list_examples(
    db: AsyncSession, *, module: str, folder: Path, examples: tuple[Example, ...]
) -> list[ExampleRead]:
    """모듈의 예시 목록: 이름 · 설명 · 줄 수 · 심은 문제와, 이미 넣었으면 그 데이터셋 번호(같은 이름)."""
    names = [example.name for example in examples]
    installed = {
        name: dataset_id
        for dataset_id, name in await db.execute(
            select(Dataset.id, Dataset.name).where(
                Dataset.module == module, Dataset.name.in_(names)
            )
        )
    }
    items = []
    for example in examples:
        answers = read_answers(folder, example)
        items.append(
            ExampleRead(
                key=example.key,
                name=example.name,
                description=example.description,
                file_name=example.file_name,
                rows=int(answers.get("rows", 0)),
                planted=[
                    ExamplePlantedRead(
                        name=str(item["name"]), count=int(item["count"]), unit=str(item["unit"])
                    )
                    for item in answers.get("planted", [])
                ],
                dataset_id=installed.get(example.name),
            )
        )
    return items


async def is_first_run_done(db: AsyncSession) -> bool:
    """처음 실행할 때 할 예시 넣기를 이미 했는지(표시가 있는지)."""
    return await db.get(Flag, FIRST_RUN_FLAG) is not None


async def has_any_dataset(db: AsyncSession) -> bool:
    """모듈과 상관없이 데이터셋이 하나라도 있는지 (예시 넣기 전부터 쓰던 DB인지 본다)."""
    return await db.scalar(select(Dataset.id).limit(1)) is not None


async def mark_first_run_done(db: AsyncSession) -> None:
    """처음 실행할 때 할 예시 넣기를 했다고 표시한다. 커밋은 부른 쪽이 한다."""
    if not await is_first_run_done(db):
        db.add(Flag(key=FIRST_RUN_FLAG))
