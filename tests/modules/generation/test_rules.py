"""검사 규칙 테스트 (DB 없이): 대화 하나를 재 문제 표시를 남기는지, 오탐을 피하는지, 가리기 · 자르기, 등급."""

from dent.modules.generation import rules
from dent.modules.generation.rules import CheckKey
from tests.modules.generation.helpers import CLEAN, conversation


def _keys(messages: list[dict[str, str]]) -> set[str]:
    return set(rules.measure(messages))


def test_measure_finds_no_problem_in_clean_conversation():
    # 실행
    marks = rules.measure(CLEAN)

    # 확인
    assert marks == {}


def test_measure_marks_broken_role_order_as_structure():
    # 준비: 사용자 말이 두 번 잇달아 나온다
    messages = [
        {"role": "user", "content": "안녕"},
        {"role": "user", "content": "거기 있어요?"},
        {"role": "assistant", "content": "네, 있습니다."},
    ]

    # 실행
    marks = rules.measure(messages)

    # 확인: 대화 전체에 걸친 문제라 자리가 없다
    assert marks[CheckKey.STRUCTURE] == [{"turn": None, "start": None, "end": None}]


def test_measure_marks_empty_prompt_and_answer():
    # 실행
    empty_prompt = _keys(conversation("", "코드는 다음과 같습니다."))
    empty_answer = _keys(conversation("요약해 주세요.", "  "))

    # 확인
    assert CheckKey.EMPTY_PROMPT in empty_prompt
    assert CheckKey.EMPTY_ANSWER in empty_answer


def test_measure_marks_truncated_answer_but_not_signature_or_hashtags():
    # 준비
    truncated = conversation(
        "이야기해 주세요.", "옛날 옛적에 한 마을에 아주 오래된 나무가 있었는데 그 나무 아래에서"
    )
    signed = conversation("편지를 써 주세요.", "잘 지내시지요? 다음 주에 뵙겠습니다.\n\nSarah 드림")
    tweet = conversation("트윗을 써 주세요.", "오늘 학회에서 발표했어요!\n#Research #Science")

    # 실행 · 확인: 끝에서 칠할 자리를 남긴다
    marks = rules.measure(truncated)
    [mark] = marks[CheckKey.TRUNCATED]
    answer = truncated[1]["content"]
    assert mark["turn"] == 1
    assert answer[mark["start"] : mark["end"]].endswith("아래에서")
    assert CheckKey.TRUNCATED not in _keys(signed)
    assert CheckKey.TRUNCATED not in _keys(tweet)


def test_measure_marks_translated_code_but_not_korean_inside_strings():
    # 준비
    translated = conversation(
        "코드를 고쳐 주세요.",
        "고친 코드입니다:\n```python\nfaces = detectMultiScale(회색, 1.3, 5)\n```",
    )
    string_inside = conversation(
        "코드를 써 주세요.",
        "```python\nif direction == '시계방향':\n    print(\"돌아갑니다\")\n```",
    )

    # 실행
    marks = rules.measure(translated)

    # 확인: 한국어가 된 인자 자리를 칠한다
    [mark] = marks[CheckKey.CODE_TRANSLATED]
    assert translated[1]["content"][mark["start"] : mark["end"]] == "detectMultiScale(회색,"
    assert CheckKey.CODE_TRANSLATED not in _keys(string_inside)


def test_measure_marks_loop_but_not_indentation_or_table_rules():
    # 준비
    looping = conversation("레시피를 써 주세요.", "감자를 으깹니다. " + "으깬 감자로 " * 20)
    table = conversation(
        "표를 만들어 주세요.", "| 이름 | 나이 |\n" + "| --- " * 8 + "|\n| 철수 | 10 |"
    )

    # 확인
    assert CheckKey.LOOP in _keys(looping)
    assert CheckKey.LOOP not in _keys(table)


def test_measure_marks_other_model_identity_unless_user_asked_about_it():
    # 준비
    identity = conversation("너는 누구야?", "저는 OpenAI가 만든 ChatGPT입니다.")
    asked = conversation(
        "ChatGPT로 목차를 만드는 법은?", "ChatGPT에 문서를 붙이고 목차를 달라고 하세요."
    )

    # 확인
    assert CheckKey.REFUSAL in _keys(identity)
    assert CheckKey.REFUSAL not in _keys(asked)


def test_measure_marks_short_answer_and_pii():
    # 준비
    short = conversation("다음 문장에 동사가 있으면 1, 없으면 0을 출력하세요.", "0.")
    pii = conversation("이메일을 요약해 주세요. 연락처: john@example.com", "채용 안내 메일입니다.")

    # 확인
    assert CheckKey.SHORT in _keys(short)
    assert CheckKey.PII in _keys(pii)


def test_mask_pii_and_cut_loop_fix_text_by_rule():
    # 실행
    masked = rules.mask_pii("연락처는 john@example.com, 010-1234-5678입니다.")
    cut = rules.cut_loop("감자를 으깹니다. " + "으깬 감자로 " * 20)

    # 확인: 루프는 첫 조각 하나만 남긴다
    assert masked == "연락처는 [이메일], [전화번호]입니다."
    assert cut == "감자를 으깹니다. 으깬 감자로"


def test_grade_of_follows_kind_and_rate_limits():
    # 확인: any는 하나라도 심각, warn_any는 주의, rate는 1% · 5% 경계
    assert rules.grade_of(CheckKey.TRUNCATED, count=1, total=1000) == "bad"
    assert rules.grade_of(CheckKey.LOOP, count=1, total=1000) == "warn"
    assert rules.grade_of(CheckKey.CODE_TRANSLATED, count=5, total=1000) == "good"
    assert rules.grade_of(CheckKey.CODE_TRANSLATED, count=30, total=1000) == "warn"
    assert rules.grade_of(CheckKey.CODE_TRANSLATED, count=60, total=1000) == "bad"


def test_safety_examples_warn_only_when_none_in_a_large_enough_sample():
    # 확인: 안전 예시가 없으면 주의지만, 판정한 대화가 적으면(우연일 수 있어) 보지 않는다
    assert rules.grade_of(CheckKey.SAFETY_EXAMPLES, count=0, total=200) == "warn"
    assert rules.grade_of(CheckKey.SAFETY_EXAMPLES, count=0, total=50) == "good"
    assert rules.grade_of(CheckKey.SAFETY_EXAMPLES, count=3, total=200) == "good"
