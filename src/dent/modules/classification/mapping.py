"""필드 맞춤 짐작: 원본의 열 이름을 보고 어느 열이 문장·라벨인지 고른다.

시스템의 미리 보기는 열 이름과 앞쪽 줄까지만 준다. 그 열을 분류의 칸에 어떻게 맞출지는 이 모듈의 규칙이다.
원본에 분할 열이 있어도 쓰지 않는다(train · valid · test를 나누지 않고 하나로 가져온다).
"""

from dent.modules.classification.schemas import FieldMappingRead

# 필드 맞춤을 짐작할 때 찾는 열 이름. 앞에 있는 이름일수록 먼저 고른다.
TEXT_COLUMN_NAMES = ("text", "sentence", "문장", "질문", "query", "title", "content", "document")
LABEL_COLUMN_NAMES = ("label", "labels", "intent", "category", "class", "라벨", "분류", "의도")


def suggest_mapping(columns: list[str]) -> FieldMappingRead:
    """열 이름으로 문장·라벨 열을 짐작한다. 못 찾으면 그 자리는 None."""
    return FieldMappingRead(
        text=_find_column(columns, TEXT_COLUMN_NAMES),
        label=_find_column(columns, LABEL_COLUMN_NAMES),
    )


def _find_column(columns: list[str], wanted_names: tuple[str, ...]) -> str | None:
    """wanted_names 순서대로 찾아 처음 맞는 열 이름을 돌려준다. 대소문자는 가리지 않는다."""
    # 대소문자만 다른 열이 여럿이면 앞의 열이 남도록 뒤에서부터 채운다.
    by_lower_name = {column.strip().lower(): column for column in reversed(columns)}
    for wanted in wanted_names:
        if wanted in by_lower_name:
            return by_lower_name[wanted]
    return None
