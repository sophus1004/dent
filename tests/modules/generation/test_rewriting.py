"""LLM 다시 쓰기 지시 테스트: 다시 쓴 응답이 지금 응답의 내용이 아니라 사용자의 지시를 따르게 묻는지."""

from dent.modules.generation import rewriting
from dent.modules.generation.rules import CheckKey

# 번역을 시켰는데 엉뚱한 영어로 답한 대화 (언어 섞임)
CONTEXT = [{"role": "user", "content": "한국어로 번역해 줘: Thank you for your patience."}]
WRONG_ANSWER = "Of course. The sentence simply means you should water the plant twice a week."


def test_rewrite_messages_ask_to_follow_the_instruction_not_the_current_answer():
    # 실행
    system, user = rewriting.rewrite_messages(CONTEXT, WRONG_ANSWER, CheckKey.LANG_MIX)

    # 확인: 지시 · 지금 응답 · 할 일을 싣고, 지금 응답이 지시와 다르면 살리지 말라고 한다
    assert "지시와 다른 내용이면 그 내용을 살리지 말고" in system.content
    assert CONTEXT[0]["content"] in user.content
    assert WRONG_ANSWER in user.content
    assert "같은 내용을" not in user.content
