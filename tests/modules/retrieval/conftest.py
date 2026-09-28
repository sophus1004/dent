"""검색 모듈 테스트 준비: 모양마다 작은 데이터셋을 끝까지 가져오는 픽스처."""

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system.models import Import
from tests.modules.retrieval.helpers import import_csv

# 세 쌍 모양의 작은 데이터: 질의 · 정답 · 오답 둘 · 원본 분할 열(가져올 때 쓰지 않는다)
TRIPLET_HEADER = ["query", "positive", "negative_1", "negative_2", "split"]
TRIPLET_ROWS = [
    [
        "환불은 며칠 걸리나요",
        "환불은 영업일 기준 3일 안에 처리됩니다.",
        "배송은 이틀 걸립니다.",
        "회원 가입은 무료입니다.",
        "train",
    ],
    [
        "배송비는 얼마인가요",
        "배송비는 3만 원 이상 무료입니다.",
        "환불은 영업일 기준 3일 안에 처리됩니다.",
        "",
        "train",
    ],
    [
        "비밀번호를 잊었어요",
        "비밀번호는 로그인 화면의 찾기에서 바꿉니다.",
        "배송은 이틀 걸립니다.",
        "",
        "test",
    ],
]
TRIPLET_MAPPING = {
    "shape": "triplet",
    "query": "query",
    "positive": "positive",
    "negatives": ["negative_1", "negative_2"],
}


@pytest.fixture(autouse=True)
async def clean_tables(db_session: AsyncSession) -> AsyncIterator[None]:
    """검색 테스트가 끝날 때마다 모든 테이블을 비운다(db_session이 끝나며 비운다)."""
    yield


@pytest.fixture
def import_triplets(db_session: AsyncSession) -> Callable[..., Awaitable[Import]]:
    """세 쌍 모양의 작은 데이터셋을 가져오는 함수를 돌려준다."""

    async def make(name: str = "상담 검색") -> Import:
        return await import_csv(
            db_session,
            header=TRIPLET_HEADER,
            rows=TRIPLET_ROWS,
            mapping=TRIPLET_MAPPING,
            name=name,
        )

    return make
