"""이어진 묶음 찾기(모든 모듈): 같은 글 · 근접 중복 쌍 · 같은 원문처럼 '함께 다녀야 하는' 항목들을 한 묶음으로 잇는다.

내보내는 파일의 묶음 칸(group)과 검색의 중복 묶음이 쓴다. 학습 · 평가로 나눌 때 묶음을 한쪽에 두면
같은 글이나 거의 같은 글이 양쪽에 들어가 평가 점수가 부풀지 않는다(묶음 단위로 나누기: GroupKFold 등).
쌍이 이어지면(가-나, 나-다) 한 묶음이다. 묶음의 대표는 가장 작은 번호라 같은 데이터면 늘 같다.
DB를 모르는 계산만 둔다.
"""

from collections.abc import Hashable, Iterable
from typing import TypeVar

Item = TypeVar("Item", bound=Hashable)


class Groups[Item]:
    """항목들의 묶음 (합치기 · 대표 찾기). 대표는 묶음에서 가장 작은 항목이다."""

    def __init__(self, items: Iterable[Item] = ()) -> None:
        self._parent: dict[Item, Item] = {item: item for item in items}

    def __contains__(self, item: object) -> bool:
        return item in self._parent

    def add(self, item: Item) -> None:
        """항목을 혼자인 묶음으로 넣는다(이미 있으면 그대로)."""
        self._parent.setdefault(item, item)

    def find(self, item: Item) -> Item:
        """항목이 든 묶음의 대표."""
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, left: Item, right: Item) -> None:
        """두 항목의 묶음을 합친다(없는 항목이면 넣고 합친다)."""
        self.add(left)
        self.add(right)
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            low, high = sorted((root_left, root_right))  # type: ignore[type-var]
            self._parent[high] = low

    def representatives(self) -> dict[Item, Item]:
        """항목마다 묶음의 대표."""
        return {item: self.find(item) for item in self._parent}
