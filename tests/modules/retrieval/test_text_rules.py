"""글 규칙 테스트: 구조 우선 나누기(모양이 다른 문서들) · 메타 모양 · 반복 문장 뼈대 · 학습 글 · 구간 떼기.

규칙이 어떤 분야도 가정하지 않는지 보려고 매뉴얼 · 마크다운 · FAQ · 번호 단위(조) 글을 함께 본다.
"""

from dent.modules.retrieval.text_rules import (
    PICK_REPEAT,
    chunk_text,
    clean_text,
    cut_spans,
    document_input,
    has_broken_text,
    meta_hits,
    meta_key,
    repeat_skeleton,
    sentence_hits,
    skip_generation_reason,
)

MANUAL = """1. 개요
이 설명서는 스마트 체중계 SC-200의 설치와 사용 방법을 설명합니다. 처음 쓰기 전에 끝까지 읽어 주세요.
2. 설치
2.1 전지 넣기
본체 뒷면의 덮개를 열고 전지 4개를 극성에 맞게 넣은 뒤 덮개를 닫습니다. 화면에 0.0이 보이면 준비가 끝난 것입니다.
2.2 앱 연결
휴대폰에서 블루투스를 켜고 앱의 기기 추가 메뉴에서 SC-200을 고릅니다. 연결되면 체중이 앱에 기록됩니다."""

MARKDOWN = """# 배포 안내
## 준비
배포 전에 테스트를 모두 통과해야 합니다. CI가 초록색인지 확인하고, 변경 기록을 적습니다. 이 과정은 모든 배포에서 같습니다.
## 올리기
태그를 만들고 이미지를 올립니다. 이미지 이름은 저장소 이름과 같게 둡니다. 올린 뒤에는 상태 화면을 5분 동안 지켜봅니다."""

FAQ = """Q. 환불은 며칠 걸리나요?
A. 환불은 영업일 기준 3일 안에 처리됩니다. 카드사 사정에 따라 하루 이틀 더 걸릴 수 있습니다.
Q. 배송비는 얼마인가요?
A. 3만 원 이상 사면 무료이고, 그 아래는 3천 원입니다. 제주와 섬 지역은 따로 더 받습니다."""

NUMBERED = """제1조(목적) 이 규정은 회사의 휴가 제도와 그 절차를 정하는 것을 목적으로 한다.
제2조(대상) 다음 각 호의 어느 하나에 해당하는 사람에게 적용한다.
1. 정규직 직원
2. 계약직 직원
제3조제2호 중 "계약직"을 "기간제"로 한다.
부칙
제1조(시행일) 이 규정은 공포한 날부터 시행한다."""


def _sections(text: str, max_tokens: int = 60) -> list[str]:
    return [chunk.section for chunk in chunk_text(text, max_tokens=max_tokens)]


def test_chunk_text_follows_outline_headings_in_manual():
    # 실행
    sections = _sections(MANUAL)

    # 확인
    assert sections == ["1. 개요", "2. 설치 › 2.1 전지 넣기", "2. 설치 › 2.2 앱 연결"]


def test_chunk_text_carries_title_only_heading_into_next_section():
    # 실행
    chunks = chunk_text(MARKDOWN, max_tokens=60)

    # 확인
    assert [chunk.section for chunk in chunks] == ["배포 안내 › 준비", "배포 안내 › 올리기"]
    assert chunks[0].text.startswith("# 배포 안내\n## 준비")


def test_chunk_text_keeps_unstructured_text_without_sections():
    # 실행
    sections = _sections(FAQ, max_tokens=40)

    # 확인
    assert sections == ["", ""]


def test_chunk_text_treats_numbered_units_as_headings_but_not_list_items():
    # 실행
    chunks = chunk_text(NUMBERED, max_tokens=30)

    # 확인
    sections = [chunk.section for chunk in chunks]
    assert "제2조(대상)" in sections
    assert "부칙 › 제1조(시행일)" in sections
    # 목록 항목("1. 정규직 직원")과 조를 가리키는 본문("제3조제2호 …")은 제목이 아니다.
    assert not any("정규직" in section or "제3조" in section for section in sections)


def test_chunk_text_keeps_last_list_item_in_its_article():
    # 준비: 조의 목록 마지막 항목 뒤에 긴 다음 조가 온다.
    text = (
        "제9조(일비) 출장 일비는 다음과 같다.\n"
        "1. 서울: 100,000원\n"
        "2. 그 밖의 지역: 80,000원\n"
        "제10조(당일 출장) 그날 돌아오는 국내 출장은 일비의 절반을 주고, 숙박비는 주지 않는다."
    )

    # 실행
    chunks = chunk_text(text, max_tokens=512)

    # 확인: 마지막 항목을 제목으로 읽어 다음 조로 옮기지 않는다.
    assert [chunk.section for chunk in chunks] == ["제9조(일비)", "제10조(당일 출장)"]
    assert chunks[0].text.endswith("2. 그 밖의 지역: 80,000원")
    assert chunks[1].text.startswith("제10조(당일 출장)")


def test_chunk_text_carries_titled_chapter_line_into_first_article():
    # 준비: 장 번호 뒤에 괄호 없이 제목이 온다.
    text = (
        "제1장 총칙\n"
        "제1조(목적) 이 규정은 회사의 정보 자산을 지키려고 임직원이 따라야 할 보안 기준을 정한다.\n"
        "제2장 정보 등급\n"
        "제5조(등급) 정보는 공개 · 내부 · 대외비의 세 등급으로 나눈다."
    )

    # 실행
    chunks = chunk_text(text, max_tokens=512)

    # 확인: 장 제목만 있는 청크는 없고, 장 제목은 첫 조의 앞과 구획 경로에 들어간다.
    assert [chunk.section for chunk in chunks] == [
        "제1장 총칙 › 제1조(목적)",
        "제2장 정보 등급 › 제5조(등급)",
    ]
    assert chunks[0].text.startswith("제1장 총칙\n제1조(목적)")


def test_meta_hits_find_contact_and_copyright_shapes():
    # 준비
    text = "사진 ⓒ한빛상사 홍길동 기자 제품을 소개했다. 문의 support@example.com · 02-123-4567"

    # 실행
    hits = {(hit.key, hit.text) for hit in meta_hits(text)}

    # 확인
    assert (meta_key("copyright"), "ⓒ한빛상사 홍길동 기자 제품을") in hits
    assert (meta_key("email"), "support@example.com") in hits
    assert (meta_key("phone"), "02-123-4567") in hits


def test_repeat_skeleton_treats_numbers_as_same():
    # 실행
    first = repeat_skeleton("이 규정은 2020년 1월 1일부터 시행한다.")
    second = repeat_skeleton("이 규정은 2024년 7월 15일부터 시행한다.")

    # 확인
    assert first == second
    assert repeat_skeleton("짧은 글") is None


def test_sentence_hits_only_returns_wanted_keys():
    # 준비
    text = "첫 문장은 내용이 있는 문장입니다. 이 규정은 2020년 1월 1일부터 시행한다."
    [_, wanted] = sentence_hits(text)

    # 실행
    hits = sentence_hits(text, {wanted.key})

    # 확인
    assert [hit.text for hit in hits] == ["이 규정은 2020년 1월 1일부터 시행한다."]


def test_document_input_puts_title_header_and_section_before_body():
    # 실행
    text = document_input(
        title="휴가 규정",
        header="인사팀",
        section="부칙 › 제1조(시행일)",
        body="제1조(시행일) 이 규정은 공포한 날부터 시행한다.",
        section_header=True,
    )

    # 확인: 본문이 마지막 구획 제목으로 시작하면 그 제목은 다시 붙이지 않는다.
    assert text == "휴가 규정 › 인사팀 › 부칙\n제1조(시행일) 이 규정은 공포한 날부터 시행한다."


def test_document_input_returns_body_when_nothing_to_add():
    # 실행
    text = document_input(title="", header="", section="장 › 절", body="본문", section_header=False)

    # 확인
    assert text == "본문"


def test_cut_spans_removes_spans_and_tidies_spaces():
    # 준비
    text = "앞 ⓒ회사 기자 본문이다.\n끝"

    # 실행
    cut = cut_spans(text, [(2, 9)])

    # 확인
    assert cut == "앞 본문이다.\n끝"


def test_skip_generation_reason_marks_mostly_repeated_piece():
    # 준비
    text = "이 규정은 공포한 날부터 시행한다. 이 규정 시행 전의 신청은 종전 규정에 따른다."

    # 실행
    reason = skip_generation_reason(text, repeat_share=0.8)

    # 확인
    assert reason == PICK_REPEAT


# ---------- 오버랩 ----------

# 구조 없는 긴 글: 문장 스물 (문장마다 토큰 약 15)
PLAIN = " ".join(f"문장 {index}번은 오버랩 시험을 위한 평범한 내용이다." for index in range(20))


def test_chunk_text_overlaps_tail_sentences_of_previous_chunk():
    # 실행
    plain = chunk_text(PLAIN, max_tokens=60)
    overlapped = chunk_text(PLAIN, max_tokens=60, overlap=20)

    # 확인
    # 다음 청크는 앞 청크의 끝 문장으로 시작하고, 겹친 몫까지 최대 토큰 안에 든다.
    first_tail = overlapped[0].text.split("\n")[-1].split(". ")[-1]
    assert overlapped[1].text.startswith(first_tail)
    assert all(chunk.tokens <= 60 for chunk in overlapped)
    # 겹친 몫만큼 본문 자리가 줄어 청크가 는다.
    assert len(overlapped) > len(plain)


def test_chunk_text_does_not_overlap_across_sections():
    # 실행
    chunks = chunk_text(MARKDOWN, max_tokens=60, overlap=20)

    # 확인
    # 새 구획의 첫 청크는 앞 구획의 글을 겹치지 않고 제목 줄로 시작한다.
    starts = {chunk.section: chunk.text for chunk in reversed(chunks)}
    assert starts["배포 안내 › 올리기"].startswith("## 올리기")


def test_chunk_text_without_overlap_is_unchanged():
    # 실행
    zero = chunk_text(MANUAL, max_tokens=60, overlap=0)
    default = chunk_text(MANUAL, max_tokens=60)

    # 확인
    assert zero == default


def test_clean_text_repairs_mis_decoded_utf8_so_nothing_broken_is_left():
    # 준비: 잘못 풀린 UTF-8(é → Ã©) · 대체 문자가 섞였다.
    text = "cafÃ© 라테를 파는 곳�"

    # 실행
    cleaned = clean_text(text)

    # 확인: 깨진 글자 탐지가 잡던 모양이 남지 않는다.
    assert has_broken_text(text)
    assert cleaned == "café 라테를 파는 곳"
    assert not has_broken_text(cleaned)
