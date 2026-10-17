"""이어진 묶음 테스트: 이어진 쌍은 한 묶음이고, 대표는 가장 작은 항목이다."""

from dent.system.groups import Groups


def test_groups_join_chained_pairs_under_smallest_item():
    # 준비: 3-5, 5-1은 이어진 쌍, 7 · 8은 따로 이어짐, 9는 혼자
    grouped = Groups[int]([9])

    # 실행
    for left, right in [(3, 5), (5, 1), (8, 7)]:
        grouped.union(left, right)

    # 확인
    assert grouped.representatives() == {9: 9, 3: 1, 5: 1, 1: 1, 8: 7, 7: 7}
    assert 5 in grouped and 2 not in grouped
