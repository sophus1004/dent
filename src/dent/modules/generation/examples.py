"""생성 모듈의 내장 예시 데이터: SFT 대화 · 상담 대조군 · 지시만 · 도구 호출 · 선호 쌍.

- sft: 글쓰기 · 요약 · 번역 · 코드 · 풀이 · 여러 턴이 섞인 messages 모양. 같은 지시 · 빈 지시 · 대화 구조 · 개인정보 ·
  빈 · 잘린 · 루프 · 긴 · 짧은 응답 · 거절 · 번역된 코드 · 언어 섞임 · 깨진 글자 · 반복을 심었고,
  뜻 분석이 있어야 보이는 근접 중복 · 지시 불일치도 심었다(정답지의 semantic).
- chat: 가상 쇼핑몰 고객센터의 두 턴 상담 대화(ShareGPT 모양, 시스템 메시지 있음). 문제를 심지 않았다.
- prompts: 응답 없이 지시만 있는 Alpaca 모양(입구 1). 응답 만들기를 해 본다. 같은 지시 · 개인정보를 조금 심었다.
- tools: 가상 쇼핑몰 상담 도구(주문 · 택배 · 상품 · 매장 · 취소)를 부르는 messages + tools. 호출 형식 문제를 심었다.
- pref: TRL 선호 모양(chosen · rejected)과 원본 점수. 같은 쌍 · 동점과, Jev가 잡는 판정 반대를 심었다.
심은 문제와 수는 example_data/<열쇠>.answers.json. 넣기는 보통 가져오기 길이다. 파일은 scripts/examples/build.py가 만든다.
"""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Shape
from dent.modules.generation.schemas import FieldMappingCreate, ImportCreate
from dent.system import datasets as datasets_service
from dent.system import examples
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import Import, ImportSource
from dent.system.schemas import DatasetUpdate, ExampleRead

# 예시 파일 · 정답지를 두는 폴더 (패키지에 함께 묶인다)
EXAMPLE_DATA_DIR = Path(__file__).parent / "example_data"

EXAMPLES: tuple[examples.Example, ...] = (
    examples.Example(
        key="sft",
        name="예시 · 지시 따르기 SFT",
        description="messages 모양 · 글쓰기 · 요약 · 번역 · 코드 · 여러 턴 · 문제 심음",
        file_name="sft.jsonl",
    ),
    examples.Example(
        key="chat",
        name="예시 · 고객 상담 대화",
        description="ShareGPT 모양 · 시스템 메시지 · 두 턴 상담 · 문제 없는 대조군",
        file_name="chat.jsonl",
    ),
    examples.Example(
        key="prompts",
        name="예시 · 지시만",
        description="Alpaca 모양 · 응답 없는 지시 · 응답 만들기 · 문제 조금 심음",
        file_name="prompts.jsonl",
    ),
    examples.Example(
        key="tools",
        name="예시 · 도구 호출 상담",
        description="messages + tools · 주문 · 택배 · 상품 도구 · 호출 형식 문제 심음",
        file_name="tools.jsonl",
    ),
    examples.Example(
        key="pref",
        name="예시 · 선호 쌍",
        description="chosen · rejected + 점수 · DPO · 같은 쌍 · 판정 반대 심음",
        file_name="pref.jsonl",
    ),
)

# 예시마다 필드 맞춤 (파일의 칸 이름)
MAPPINGS: dict[str, FieldMappingCreate] = {
    "sft": FieldMappingCreate(shape=Shape.MESSAGES, messages="messages"),
    "chat": FieldMappingCreate(shape=Shape.SHAREGPT, messages="conversations"),
    "prompts": FieldMappingCreate(shape=Shape.ALPACA, instruction="instruction", input="input"),
    "tools": FieldMappingCreate(shape=Shape.MESSAGES, messages="messages", tools="tools"),
    "pref": FieldMappingCreate(
        shape=Shape.PREFERENCE,
        chosen="chosen",
        rejected="rejected",
        chosen_score="score_chosen",
        rejected_score="score_rejected",
    ),
}


async def list_examples(db: AsyncSession) -> list[ExampleRead]:
    """생성 예시 목록과 이미 넣은 데이터셋 번호."""
    return await examples.list_examples(
        db, module=generation_service.MODULE_NAME, folder=EXAMPLE_DATA_DIR, examples=EXAMPLES
    )


async def install_example(db: AsyncSession, *, key: str) -> Import:
    """예시를 새 데이터셋으로 가져오기 시작한다(작업을 넣는다).

    없는 예시면 NotFoundError, 이미 넣었으면(같은 이름의 데이터셋) ConflictError.
    """
    example = examples.find_example(EXAMPLES, key=key)
    await examples.ensure_not_installed(db, module=generation_service.MODULE_NAME, example=example)
    upload_id = await examples.register_file(db, folder=EXAMPLE_DATA_DIR, example=example)
    import_row = await generation_service.create_import(
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
