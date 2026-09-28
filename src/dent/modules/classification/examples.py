"""분류 모듈의 내장 예시 데이터: 설치하자마자 진단 · 도우미 · 뜻 분석을 해 볼 수 있는 데이터셋 둘.

- topics: 백과 문장 7주제. 원본 분할 열이 있고(하나로 합쳐진다), 진단의 모든 검사가 뜨도록 문제를 심었다
  (심은 문제와 수는 example_data/topics.answers.json).
- intents: 쇼핑몰 고객 문의 8의도(엑셀). 문제가 없는 대조군.
넣기는 보통 가져오기 길(create_import → 작업)이다. 파일은 scripts/examples/build.py가 만든다.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import service as classification_service
from dent.modules.classification.schemas import FieldMappingCreate, ImportCreate
from dent.system import datasets as datasets_service
from dent.system import examples
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import Import, ImportSource
from dent.system.schemas import DatasetUpdate, ExampleRead

# 예시 파일 · 정답지를 두는 폴더 (패키지에 함께 묶인다)
EXAMPLE_DATA_DIR = Path(__file__).parent / "example_data"

EXAMPLES: tuple[examples.Example, ...] = (
    examples.Example(
        key="topics",
        name="예시 · 백과 문장 주제",
        description="백과 문장 · 7주제 · 원본 분할 열 · 문제 심음",
        file_name="topics.csv",
    ),
    examples.Example(
        key="intents",
        name="예시 · 고객 문의 의도",
        description="쇼핑몰 고객 문의 · 8의도 · 엑셀 · 문제 없음",
        file_name="intents.xlsx",
        sheet="문의",
    ),
)

# 예시마다 필드 맞춤 (파일의 열 이름)
MAPPINGS: dict[str, FieldMappingCreate] = {
    "topics": FieldMappingCreate(text="문장", label="주제"),
    "intents": FieldMappingCreate(text="문의", label="의도"),
}


async def list_examples(db: AsyncSession) -> list[ExampleRead]:
    """분류 예시 목록과 이미 넣은 데이터셋 번호."""
    return await examples.list_examples(
        db,
        module=classification_service.MODULE_NAME,
        folder=EXAMPLE_DATA_DIR,
        examples=EXAMPLES,
    )


async def install_example(db: AsyncSession, *, key: str) -> Import:
    """예시를 새 데이터셋으로 가져오기 시작한다(작업을 넣는다).

    없는 예시면 NotFoundError, 이미 넣었으면(같은 이름의 데이터셋) ConflictError.
    """
    example = examples.find_example(EXAMPLES, key=key)
    await examples.ensure_not_installed(
        db, module=classification_service.MODULE_NAME, example=example
    )
    upload_id = await examples.register_file(db, folder=EXAMPLE_DATA_DIR, example=example)
    import_row = await classification_service.create_import(
        db,
        data=ImportCreate(
            new_dataset_name=example.name,
            source=ImportSource.FILE,
            upload_id=upload_id,
            sheet=example.sheet,
            mapping=MAPPINGS[key],
        ),
    )
    await datasets_service.update_dataset(
        db, dataset_id=import_row.dataset_id, data=DatasetUpdate(description=example.description)
    )
    return import_row


async def install_all(db: AsyncSession) -> int:
    """넣지 않은 내장 예시를 모두 새 데이터셋으로 가져오기 시작한다(예시마다 커밋). 넣기 시작한 수.

    처음 실행할 때 작업 실행기가 부른다. 이미 넣은 예시(같은 이름의 데이터셋)나 파일이 없는 예시는 건너뛴다.
    """
    started = 0
    for example in EXAMPLES:
        try:
            await install_example(db, key=example.key)
        except (ConflictError, NotFoundError):
            await db.rollback()
            continue
        await db.commit()
        started += 1
    return started
