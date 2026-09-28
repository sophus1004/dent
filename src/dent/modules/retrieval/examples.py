"""검색 모듈의 내장 예시 데이터: 흐름의 세 입구(문서만 · 질문과 지문 · 질의와 오답)를 모두 해 볼 수 있는 데이터셋 넷.

- encyclopedia: 백과 문서만(1단계 입구). 긴 문서 · 반복 꼬리말(출처 · 이메일 · 저작권) · 깨진 글자 · 같은 본문 · 목차 · 표를 심었다.
- reading: 질문 · 지문 · 답(MRC, 2단계 입구). 원본 분할 열 · 문맥 의존 · 짧은 · 긴 질의 · 쉬운 쌍 · 같은 질의 · 긴 지문을 심었다.
- triplets: 질의 · 정답 · 오답(3단계 입구). 판정 충돌 · 정답과 거의 같은 오답 · 거짓 오답 · 쉬운 오답을 심었다.
- company: 가상 회사 규정 · FAQ(문서만). 제N장 · 제N조 구조와 반복 서명 — 규칙이 분야를 가정하지 않는다는 것을 보인다.
심은 문제와 수는 example_data/<열쇠>.answers.json. 넣기는 보통 가져오기 길이다. 파일은 scripts/examples/build.py가 만든다.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import Shape
from dent.modules.retrieval.schemas import FieldMappingCreate, ImportCreate
from dent.system import datasets as datasets_service
from dent.system import examples
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import Import, ImportSource
from dent.system.schemas import DatasetUpdate, ExampleRead

# 예시 파일 · 정답지를 두는 폴더 (패키지에 함께 묶인다)
EXAMPLE_DATA_DIR = Path(__file__).parent / "example_data"

EXAMPLES: tuple[examples.Example, ...] = (
    examples.Example(
        key="encyclopedia",
        name="예시 · 백과 문서",
        description="백과 문서만 · 1단계 입구 · 긴 문서 · 반복 꼬리말 · 문제 심음",
        file_name="encyclopedia.csv",
    ),
    examples.Example(
        key="reading",
        name="예시 · 질문과 지문",
        description="질문 · 지문 · 답 · 2단계 입구 · 원본 분할 열 · 문제 심음",
        file_name="reading.csv",
    ),
    examples.Example(
        key="triplets",
        name="예시 · 질의와 오답",
        description="질의 · 정답 · 오답 · 3단계 입구 · 문제 심음",
        file_name="triplets.csv",
    ),
    examples.Example(
        key="company",
        name="예시 · 회사 규정과 FAQ",
        description="가상 회사 규정 · FAQ · 문서만 · 제N조 구조 · 반복 서명",
        file_name="company.csv",
    ),
)

# 예시마다 필드 맞춤 (파일의 열 이름)
MAPPINGS: dict[str, FieldMappingCreate] = {
    "encyclopedia": FieldMappingCreate(
        shape=Shape.DOCUMENTS, text="본문", title="제목", header_columns=["분야"]
    ),
    "reading": FieldMappingCreate(
        shape=Shape.MRC, query="질문", positive="지문", answer="답", title="제목"
    ),
    "triplets": FieldMappingCreate(
        shape=Shape.TRIPLET, query="질의", positive="정답", negatives=["오답1", "오답2", "오답3"]
    ),
    "company": FieldMappingCreate(
        shape=Shape.DOCUMENTS, text="본문", title="제목", header_columns=["종류"]
    ),
}


async def list_examples(db: AsyncSession) -> list[ExampleRead]:
    """검색 예시 목록과 이미 넣은 데이터셋 번호."""
    return await examples.list_examples(
        db, module=retrieval_service.MODULE_NAME, folder=EXAMPLE_DATA_DIR, examples=EXAMPLES
    )


async def install_example(db: AsyncSession, *, key: str) -> Import:
    """예시를 새 데이터셋으로 가져오기 시작한다(작업을 넣는다).

    없는 예시면 NotFoundError, 이미 넣었으면(같은 이름의 데이터셋) ConflictError.
    """
    example = examples.find_example(EXAMPLES, key=key)
    await examples.ensure_not_installed(db, module=retrieval_service.MODULE_NAME, example=example)
    upload_id = await examples.register_file(db, folder=EXAMPLE_DATA_DIR, example=example)
    import_row = await retrieval_service.create_import(
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
