"""평가셋 오염 대조 테스트: 8-gram 몫이 절반을 넘으면 겹침, 짧은 문항은 통째로 이어져 있을 때만,
뜻으로 견줄 후보는 임베딩 코사인이 하한을 넘는 평가 문항(가까운 쌍부터)."""

import numpy as np

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


def test_semantic_candidates_keep_close_eval_items_from_the_nearest_pair():
    # 준비: 학습 글 둘, 평가 문항 둘. 학습 0은 문항 0과 0.95 · 문항 1과 0.29, 학습 1은 문항 0과 0 · 문항 1과 0.954.
    train = np.array([[0.95, 0.3122, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    evals = np.array([[1.0, 0.0, 0.0], [0.3, 0.0, 0.954]], dtype=np.float32)

    # 실행
    found = contamination.semantic_candidates(train, evals)

    # 확인: 하한(0.65)을 넘는 쌍만, 가까운 것부터
    assert [(c.train, c.eval) for c in found] == [(1, 1), (0, 0)]
    assert found[0].similarity > found[1].similarity
