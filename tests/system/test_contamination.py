"""평가셋 오염 대조 테스트: 8-gram 몫이 절반을 넘으면 겹침, 짧은 문항은 통째로 이어져 있을 때만."""

from dent.system import contamination

# 평가 문항들 (번호, 글)
EVAL = [
    (1, "다음 문장을 읽고 글쓴이가 가장 강조하는 생각을 한 문장으로 쓰시오 오늘은 비가 온다"),
    (2, "대한민국의 수도는 어디입니까"),
    (3, "안녕하세요"),
]


def test_best_match_finds_training_text_that_copies_most_of_an_eval_item():
    # 준비
    index = contamination.EvalIndex(EVAL)
    copied = "문제: 다음 문장을 읽고 글쓴이가 가장 강조하는 생각을 한 문장으로 쓰시오 오늘은 비가 온다. 답은?"

    # 실행
    match = index.best_match(copied)

    # 확인
    assert match is not None
    assert match.eval_id == 1
    assert match.coverage == 1.0


def test_best_match_ignores_small_overlap_and_too_short_items():
    # 준비
    index = contamination.EvalIndex(EVAL)
    unrelated = "오늘은 비가 온다고 해서 우산을 챙겼다. 안녕하세요 하고 인사했다."

    # 실행 · 확인: 앞 문항과 몇 낱말만 겹치고, '안녕하세요'(3낱말 미만)는 보지 않는다
    assert index.best_match(unrelated) is None
    assert len(index) == 2


def test_best_match_finds_short_item_only_when_its_words_appear_together():
    # 준비
    index = contamination.EvalIndex(EVAL)

    # 실행
    found = index.best_match("퀴즈입니다. 대한민국의 수도는 어디입니까? 맞혀 보세요")
    apart = index.best_match("대한민국의 역사와 수도 서울은 어디입니까")

    # 확인
    assert found == contamination.Match(eval_id=2, coverage=1.0)
    assert apart is None
