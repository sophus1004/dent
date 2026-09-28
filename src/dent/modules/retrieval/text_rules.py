"""검색 모듈의 글 규칙: 토큰 수 어림, 학습 글, 구조 우선 나누기, 반복 구간(문장 · 메타 모양), 깨진 글자,
질의 유형, 문맥 의존 질의, 쉬운 쌍.

모두 규칙(정규식 · 셈)이고 DB를 모른다. 진단(overview.py) · 고치기(edits.py) · 반복 구간(repeats.py) · 도우미가 같이 쓴다.
규칙은 글의 모양만 본다: 반복 · 자리 · 길이 · 구조 표지(제목 줄 · 번호 표지) · 메타 모양(이메일 · 주소 · 저작권 기호).
어떤 분야의 글인지는 가정하지 않는다. 뜻을 봐야 하는 판단(이 반복이 서명인가 내용인가)은 사람이 고른다.
토큰 수는 학습할 모델의 토크나이저가 아니라 글자로 어림한다(한국어는 글자당 토큰이 많고, 영어는 적다).
"""

import itertools
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from dent.system.text import make_text_hash, normalize_text

# 토큰 수 어림: 한글 한 글자 · 그 밖 글자 몇 개가 토큰 하나인지 (XLM-R 계열 토크나이저의 대략)
HANGUL_CHARS_PER_TOKEN = 1.4
OTHER_CHARS_PER_TOKEN = 3.6

# 나누기에서 너무 짧은 청크의 토큰 수. 이보다 짧으면 같은 구획의 앞 청크에 붙인다.
MIN_CHUNK_TOKENS = 40

# 학습 글의 머리말 구분 (제목 › 머리말 칸 › 구획 제목)
HEADER_SEPARATOR = " › "

# 구획 제목 한 칸 · 구획 경로 전체의 최대 글자 수
SECTION_PART_MAX = 60
SECTION_MAX = 200

# 나눌 때 청크의 머리말(구획 경로)에 남겨 두는 토큰 수
SECTION_RESERVE_TOKENS = 24

# 제목 줄로 보는 줄의 최대 글자 수 (이보다 길면 본문이다)
HEADING_MAX_CHARS = 40

# 반복 구간으로 세는 문장의 글자 수 범위 (공백 뺌). 짧은 것은 우연히 겹치고, 긴 것은 본문이다.
REPEAT_MIN_CHARS = 10
REPEAT_MAX_CHARS = 300

# 메타 모양 · 반복 문장이 앞머리 · 꼬리에 있다고 보는 글자 수
EDGE_CHARS = 120

# 저작권 기호 뒤에 함께 떼는 최대 낱말 수 (기호에 붙은 낱말 + 이만큼)
COPYRIGHT_TAIL_WORDS = 3

# 청크의 이만큼 이상이 반복 구간이면 질의를 만들지 않는다
REPEAT_SKIP_SHARE = 0.7

# 짧은 질의의 기준 글자 수
SHORT_QUERY_CHARS = 5

# 쉬운 쌍: 질의의 글자 두 개 묶음이 정답 문서에 이만큼 들어 있으면 거의 베낀 것이다
EASY_PAIR_OVERLAP = 0.9

# 쉬운 쌍을 재는 가장 짧은 질의 (너무 짧으면 겹치기 쉽다)
EASY_PAIR_MIN_CHARS = 10

# 표 · 목차로 보는 청크: 표 칸 나눔(|, 탭)이 있는 줄 · 쪽 번호로 끝나는 줄의 비율
TABLE_LINE_SHARE = 0.5
TOC_LINE_SHARE = 0.5

# 서명란 · 빈 청크로 보는 짧은 청크의 글자 수
TINY_CHUNK_CHARS = 30

# 청크 고르기의 까닭 (문서 표시 marks.pick)
PICK_SHORT = "짧은 청크"
PICK_EMPTY = "빈 청크"
PICK_TABLE = "표만"
PICK_TOC = "목차"
PICK_REPEAT = "반복 구간"

# 메타 모양의 종류와 이름. 내용과 상관없는 정보의 모양이다(분야와 상관없다).
META_NAMES = {
    "email": "이메일",
    "url": "웹 주소",
    "phone": "전화번호",
    "copyright": "저작권 표시",
    "notice": "전재 금지",
}

# 반복 구간 열쇠의 앞머리: 메타 모양은 'meta:<종류>', 문장은 뼈대의 해시
META_KEY_PREFIX = "meta:"

# 깨진 글자: 대체 문자 · 잘못 풀린 UTF-8(Ã, Â 등이 이어짐) · 조절 문자
_BROKEN = re.compile("�|[ÃÂ][\u0080-¿]|[\x00-\x08\x0b\x0c\x0e-\x1f]")

# 잘못 풀린 UTF-8 조각: UTF-8의 첫 바이트를 Latin-1로 읽은 글자(À-ÿ) 뒤에 이어지는 바이트 글자들 (예: é → Ã©)
_MOJIBAKE_RUN = re.compile("[À-ÿ][\u0080-¿]+")

# HTML · 마크다운 찌꺼기
_MARKUP = re.compile(
    r"</?(?:p|br|div|span|td|tr|li|ul|b|i|strong|em)\b[^>]*>|&(?:nbsp|amp|lt|gt|quot);",
    re.IGNORECASE,
)

# 메타 모양 (종류마다)
_META_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "url": re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+"),
    "phone": re.compile(
        r"(?<![\d.])(?:\+\d{1,3}[-.\s])?\(?0\d{1,2}\)?[-.\s]\d{3,4}[-.\s]\d{4}(?![\d.])"
    ),
    "copyright": re.compile(
        r"(?:ⓒ|©|Copyright\b)\S*(?:\s+\S+){0," + str(COPYRIGHT_TAIL_WORDS) + "}", re.IGNORECASE
    ),
    "notice": re.compile(
        r"무단\s*전재(?:\s*(?:및|와|,|·|ㆍ)?\s*재배포\s*금지)?|재배포\s*금지|All rights reserved\.?",
        re.IGNORECASE,
    ),
}

# 문장 · 줄 경계 (반복 문장을 셀 때의 단위)
_SEGMENT_BREAK = re.compile(r"(?<=[.!?。！？])\s+|\n+")

# 반복 문장의 뼈대: 숫자 · 동그라미 숫자는 같은 것으로 본다(번호 · 날짜만 다른 문장이 같은 틀이 된다)
_NUMBERS = re.compile(r"[\d①-⑳]+")

# 문맥 의존 질의: 문서 없이는 가리키는 것을 알 수 없는 말
_CONTEXT_DEPENDENT = re.compile(
    r"^(?:그|그녀|그들|이들|이것|그것|저것)(?:는|은|이|가|의|를|을)?\s|"
    r"(?:이|위|아래|본|해당)\s*(?:글|기사|문서|내용|규정|조항|표|문단|지문)",
)

# 질의 유형을 가르는 말
_WHO = re.compile(r"누구|누가|어느\s*사람|무엇|뭐|무슨|어떤\s")
_WHEN_WHERE = re.compile(r"언제|몇\s*(?:년|월|일|시)|어디|어느\s*(?:곳|나라|지역|도시)")
_WHY_HOW = re.compile(r"왜|어떻게|이유|방법|까닭|배경")
_QUESTION_END = re.compile(r"[?？]$|(?:인가|인지|는가|나요|까|니|냐)\s*[?？]?$")

# 쪽 번호로 끝나는 목차 줄 (예: "3장 설치 …… 12")
_TOC_LINE = re.compile(r"(?:\.{3,}|…+|·{3,}|\s{3,})\s*\d{1,4}\s*$")

# 구조 표지: 마크다운 제목 · "제N편/장/절/관/조" 번호(괄호 제목) · 개요 번호(1. · 1.1) · 항목 표지
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
# 번호 단위 뒤에는 공백 · 괄호 · 줄 끝만 온다("제3조제4호 …"처럼 본문이 조를 가리키는 줄은 제목이 아니다).
_NUMBERED_UNIT = re.compile(
    r"^(제\s*\d+\s*(편|장|절|관|조)(?:\s*의\s*\d+)?)(?=\s|\(|$)\s*(\([^)\n]{1,40}\))?"
)
_OUTLINE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+\S")
_ITEM = re.compile(r"^(?:[①-⑳]|[가-하][.)]|\(\d+\)|\d+[.)]|[-•·*]\s)")

# 번호 단위처럼 시작하지만 번호 단위가 아닌 줄 ("제2조부터 …", "제3조제4호 …")은 본문이다
_NUMBERED_START = re.compile(r"^제\s*\d+\s*(?:편|장|절|관|조)")
_LINE_END_PUNCTUATION = re.compile(r"[.!?。！？:;,]$")

# 번호 단위의 깊이 (작을수록 큰 구획)
NUMBERED_UNIT_LEVELS = {"편": 1, "장": 2, "절": 3, "관": 4, "조": 5}

# 문장 경계 (긴 줄을 나눌 때)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+")


# ---------- 토큰 · 깨진 글자 · 정리 ----------


def estimate_tokens(text: str) -> int:
    """토큰 수 어림 (한글과 그 밖 글자를 따로 센다)."""
    hangul = sum(1 for char in text if "가" <= char <= "힣")
    other = sum(1 for char in text if not char.isspace()) - hangul
    return math.ceil(hangul / HANGUL_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN)


def has_broken_text(text: str) -> bool:
    """깨진 글자(대체 문자 · 잘못 풀린 UTF-8 · 조절 문자)나 HTML 찌꺼기가 있는지."""
    return bool(_BROKEN.search(text) or _MARKUP.search(text))


def clean_text(text: str) -> str:
    """글자 정리: 잘못 풀린 UTF-8 되돌리기(안 되면 지움), NFC 정규화, HTML 찌꺼기 · 조절 문자 지우기,
    줄 안 공백 줄이기(줄 나눔은 남긴다). has_broken_text가 잡는 모양은 모두 없앤다."""
    cleaned = _MOJIBAKE_RUN.sub(_repair_mojibake, text)
    cleaned = unicodedata.normalize("NFC", cleaned).replace("�", "")
    cleaned = _MARKUP.sub(" ", cleaned)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    return "\n".join(line for line in lines).strip()


def _repair_mojibake(found: re.Match[str]) -> str:
    """잘못 풀린 UTF-8 조각을 원래 글자로 되돌린다. 되돌릴 수 없으면 지운다."""
    try:
        return found.group(0).encode("latin-1").decode("utf-8")
    except UnicodeError:
        return ""


def lines_of(text: str) -> list[str]:
    """비지 않은 줄들 (앞뒤 공백 뺌)."""
    return [line.strip() for line in text.splitlines() if line.strip()]


# ---------- 학습 글 ----------


def document_input(
    *, title: str, header: str, section: str, body: str, section_header: bool
) -> str:
    """학습 글: 머리말(제목 › 머리말 칸 › 구획 경로) + 본문. 머리말이 없으면 본문 그대로.

    본문이 구획 경로의 마지막 제목으로 시작하면 그 제목은 머리말에 다시 붙이지 않는다.
    임베딩 · 오답 찾기 · Jev 판정 · 내보내기가 모두 이 글을 쓴다(학습 글 = 색인 글).
    """
    parts = [part for part in (title.strip(), header.strip()) if part]
    if section_header and section:
        path = section.split(HEADER_SEPARATOR)
        if body.lstrip().startswith(path[-1]):
            path = path[:-1]
        parts += path
    prefix = HEADER_SEPARATOR.join(parts)
    return f"{prefix}\n{body}" if prefix else body


def cut_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """글에서 구간들을 떼고 남은 공백을 줄인다(줄 나눔은 남긴다)."""
    ordered = sorted(spans)
    if not ordered:
        return text
    pieces = []
    position = 0
    for start, end in ordered:
        if end <= position:
            continue
        pieces.append(text[position : max(start, position)])
        position = end
    pieces.append(text[position:])
    joined = "".join(pieces)
    lines = [" ".join(line.split()) for line in joined.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------- 반복 구간 (문장 · 메타 모양) ----------


@dataclass(frozen=True)
class Segment:
    """글의 문장 또는 줄 하나와 자리."""

    # 글 안에서의 시작 · 끝 자리
    start: int
    end: int

    # 문장 · 줄 글 (앞뒤 공백 뺌)
    text: str


@dataclass(frozen=True)
class RepeatHit:
    """글 안에서 찾은 반복 구간 하나: 열쇠 · 자리 · 원문."""

    # 반복 구간 열쇠 (문장 뼈대의 해시 또는 meta:<종류>)
    key: str
    start: int
    end: int
    text: str


def segments(text: str) -> list[Segment]:
    """글을 문장 · 줄 단위로 나눈다 (빈 것 뺌)."""
    found = []
    position = 0
    for match in [*_SEGMENT_BREAK.finditer(text), None]:
        end = match.start() if match else len(text)
        piece = text[position:end]
        stripped = piece.strip()
        if stripped:
            start = position + (len(piece) - len(piece.lstrip()))
            found.append(Segment(start=start, end=start + len(stripped), text=stripped))
        if match:
            position = match.end()
    return found


def repeat_skeleton(segment: str) -> str | None:
    """반복 문장으로 셀 뼈대(숫자는 0으로). 너무 짧거나 길면 None."""
    compact_length = len("".join(segment.split()))
    if not REPEAT_MIN_CHARS <= compact_length <= REPEAT_MAX_CHARS:
        return None
    return _NUMBERS.sub("0", normalize_text(segment))


def sentence_key(skeleton: str) -> str:
    """반복 문장의 열쇠 (뼈대의 해시)."""
    return make_text_hash(skeleton)


def meta_key(kind: str) -> str:
    """메타 모양의 열쇠."""
    return f"{META_KEY_PREFIX}{kind}"


def meta_hits(text: str) -> list[RepeatHit]:
    """글 안의 메타 모양 구간들 (종류마다, 앞에서부터)."""
    hits = []
    for kind, pattern in _META_PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append(
                RepeatHit(
                    key=meta_key(kind), start=match.start(), end=match.end(), text=match.group()
                )
            )
    return sorted(hits, key=lambda hit: (hit.start, hit.end))


def sentence_hits(text: str, keys: set[str] | None = None) -> list[RepeatHit]:
    """글 안의 문장마다 뼈대 열쇠. keys가 있으면 그 열쇠의 문장만."""
    hits = []
    for segment in segments(text):
        skeleton = repeat_skeleton(segment.text)
        if skeleton is None:
            continue
        key = sentence_key(skeleton)
        if keys is None or key in keys:
            hits.append(RepeatHit(key=key, start=segment.start, end=segment.end, text=segment.text))
    return hits


def is_edge(hit: RepeatHit, text_length: int) -> tuple[bool, bool]:
    """구간이 글의 앞머리 · 꼬리에 있는지."""
    return hit.start < EDGE_CHARS, hit.end > text_length - EDGE_CHARS


def covered_chars(spans: Iterable[tuple[int, int]]) -> int:
    """구간들이 덮는 글자 수 (겹침은 한 번만)."""
    total = 0
    reach = 0
    for start, end in sorted(spans):
        if end <= reach:
            continue
        total += end - max(start, reach)
        reach = end
    return total


# ---------- 청크 고르기 ----------


def skip_generation_reason(text: str, *, repeat_share: float = 0.0) -> str | None:
    """질의를 만들지 않을 청크인지: 표만 · 목차 · 서명란(아주 짧음) · 반복 구간이 대부분. 아니면 None."""
    lines = lines_of(text)
    if len(text.strip()) < TINY_CHUNK_CHARS:
        return PICK_SHORT
    if not lines:
        return PICK_EMPTY
    table_lines = sum(1 for line in lines if line.count("|") >= 2 or "\t" in line)
    if table_lines / len(lines) >= TABLE_LINE_SHARE:
        return PICK_TABLE
    toc_lines = sum(1 for line in lines if _TOC_LINE.search(line))
    if toc_lines / len(lines) >= TOC_LINE_SHARE:
        return PICK_TOC
    if repeat_share >= REPEAT_SKIP_SHARE:
        return PICK_REPEAT
    return None


# ---------- 나누기 (구조 우선) ----------


@dataclass(frozen=True)
class Chunk:
    """나눈 청크 하나."""

    # 청크 본문
    text: str

    # 토큰 수 어림
    tokens: int

    # 청크가 속한 구획 경로 (제목 › 제목). 구조가 없으면 빈 글.
    section: str = ""


def chunk_text(text: str, *, max_tokens: int, overlap: int = 0) -> list[Chunk]:
    """문서를 max_tokens 안의 청크로 나눈다. 구조 표지가 있으면 구획부터, 없으면 줄(문단) → 문장 경계로.

    구획은 제목 줄(마크다운 # · 짧고 문장 부호로 끝나지 않는 줄 · 개요 번호) · 번호 단위(제N장 · 제N조 …)로
    모양만 보고 찾는다. 구획을 넘어 합치지 않고, 같은 구획 안의 너무 짧은 청크만 앞 청크에 붙인다.
    한 문장이 max_tokens보다 길면 그 문장만 글자 수로 자른다.
    overlap(토큰)을 주면 같은 구획의 앞 청크 끝 문장들(overlap 안)을 다음 청크 앞에 겹친다(구획을 넘어 겹치지 않는다).
    겹친 몫까지 max_tokens 안에 들도록 본문 자리를 그만큼 줄여 담는다.
    """
    overlap = max(0, min(overlap, max_tokens // 2))
    budget = max_tokens - overlap
    chunks: list[Chunk] = []
    for section, lines in _sections(lines_of(text)):
        packed = _pack(lines, max_tokens=budget, section=section)
        chunks += _overlap(packed, overlap=overlap) if overlap else packed
    return chunks


def _sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """줄들을 구획으로 묶는다: [(구획 경로, 줄들)]. 제목 줄도 본문에 남긴다(원문을 지키려고).

    제목 줄만 있고 곧바로 아래 제목이 오면(# 큰 제목 → ## 작은 제목) 그 줄은 아래 구획의 맨 앞에 붙인다.
    """
    sections: list[tuple[str, list[str]]] = [("", [])]
    path: list[tuple[int, str]] = []
    heading_only = False
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else None
        previous = lines[index - 1] if index > 0 else None
        heading = _heading(line, following, previous)
        if heading is None:
            sections[-1][1].append(line)
            heading_only = False
            continue
        level, label = heading
        path = [part for part in path if part[0] < level] + [(level, label[:SECTION_PART_MAX])]
        joined = HEADER_SEPARATOR.join(part[1] for part in path)[:SECTION_MAX]
        carried = sections.pop()[1] if heading_only else []
        sections.append((joined, [*carried, line]))
        # 제목 뒤에 본문이 같은 줄에 있으면("제1조(목적) 이 법은 …") 제목만 있는 구획이 아니다.
        heading_only = _compact(line).removeprefix("#").lstrip("#") == _compact(label)
    return [(section, lines) for section, lines in sections if lines]


def _compact(text: str) -> str:
    return "".join(text.split())


def _heading(
    line: str, following: str | None, previous: str | None = None
) -> tuple[int, str] | None:
    """제목 줄이면 (깊이, 제목), 아니면 None.

    짧고 문장 부호로 끝나지 않는 줄이 본문 앞에 오면 제목이다. 개요 번호(1. · 1.1)는 앞 · 다음 줄이 같은 깊이의
    번호면 목록의 항목으로 보고, 더 깊은 번호나 본문이 올 때만 제목으로 본다(목록의 마지막 항목 뒤에 다음 조가
    와도 그 항목을 제목으로 읽어 다음 구획으로 옮기지 않게).
    """
    markdown = _MARKDOWN_HEADING.match(line)
    if markdown:
        return len(markdown.group(1)), markdown.group(2).strip()
    numbered = _NUMBERED_UNIT.match(line)
    if numbered:
        label = re.sub(r"\s+", "", numbered.group(1)) + (numbered.group(3) or "")
        rest = line[numbered.end() :].strip()
        # "제1장 총칙"처럼 편 · 장 · 절 · 관 번호 뒤의 짧은 글은 제목이다(조는 "제1조(목적) 이 법은 …"처럼 본문이 온다).
        is_titled_unit = (
            numbered.group(2) != "조"
            and bool(rest)
            and len(line) <= HEADING_MAX_CHARS
            and not _LINE_END_PUNCTUATION.search(line)
        )
        if is_titled_unit:
            label = f"{label} {rest}"
        return NUMBERED_UNIT_LEVELS[numbered.group(2)], label
    is_short = len(line) <= HEADING_MAX_CHARS and not _LINE_END_PUNCTUATION.search(line)
    if not is_short or following is None:
        return None
    next_outline = _OUTLINE.match(following)
    is_body_next = (
        len(following) > HEADING_MAX_CHARS and next_outline is None and not _ITEM.match(following)
    )
    outline = _OUTLINE.match(line)
    if outline:
        depth = outline.group(1).count(".")
        previous_outline = _OUTLINE.match(previous) if previous else None
        is_list_continued = (
            previous_outline is not None and previous_outline.group(1).count(".") == depth
        )
        if is_list_continued:
            return None
        is_deeper_next = next_outline is not None and next_outline.group(1).count(".") > depth
        # "1." → 2, "1.1" → 3: 맨 위는 번호 없는 제목 줄(1)에 남겨 둔다.
        return (2 + depth, line) if is_deeper_next or is_body_next else None
    if _ITEM.match(line) or _NUMBERED_START.match(line):
        return None
    is_heading_next = bool(
        _MARKDOWN_HEADING.match(following) or _NUMBERED_UNIT.match(following) or next_outline
    )
    return (1, line) if is_body_next or is_heading_next else None


def _pack(lines: list[str], *, max_tokens: int, section: str) -> list[Chunk]:
    """한 구획의 줄들을 max_tokens 안의 청크로 담는다. 너무 짧은 청크는 앞 청크에 붙인다."""
    units: list[str] = []
    for line in lines:
        if estimate_tokens(line) <= max_tokens:
            units.append(line)
            continue
        for sentence in _SENTENCE_BREAK.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            if estimate_tokens(sentence) <= max_tokens:
                units.append(sentence)
            else:
                units.extend(_hard_split(sentence, max_tokens=max_tokens))
    groups: list[list[str]] = []
    size = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if groups and size + unit_tokens <= max_tokens:
            groups[-1].append(unit)
            size += unit_tokens
        else:
            groups.append([unit])
            size = unit_tokens
    merged: list[Chunk] = []
    for parts in groups:
        joined = "\n".join(parts)
        chunk = Chunk(text=joined, tokens=estimate_tokens(joined), section=section)
        is_tiny = chunk.tokens < MIN_CHUNK_TOKENS
        if merged and is_tiny and merged[-1].tokens + chunk.tokens <= max_tokens:
            text_joined = f"{merged[-1].text}\n{chunk.text}"
            merged[-1] = Chunk(
                text=text_joined, tokens=estimate_tokens(text_joined), section=section
            )
        else:
            merged.append(chunk)
    return merged


def _overlap(chunks: list[Chunk], *, overlap: int) -> list[Chunk]:
    """한 구획의 청크마다 앞 청크의 끝(overlap 토큰 안)을 앞에 붙인다. 첫 청크는 그대로다."""
    joined = chunks[:1]
    for previous, chunk in itertools.pairwise(chunks):
        tail = _tail(previous.text, max_tokens=overlap)
        text = f"{tail}\n{chunk.text}" if tail else chunk.text
        joined.append(Chunk(text=text, tokens=estimate_tokens(text), section=chunk.section))
    return joined


def _tail(text: str, *, max_tokens: int) -> str:
    """글 끝의 문장들 가운데 max_tokens 안에 드는 만큼 (원문 그대로). 끝 문장 하나가 더 길면 끝 낱말들만."""
    pieces = segments(text)
    start = None
    size = 0
    for piece in reversed(pieces):
        tokens = estimate_tokens(piece.text)
        if size + tokens > max_tokens:
            break
        start = piece.start
        size += tokens
    if start is not None:
        return text[start:].strip()
    words: list[str] = []
    for word in reversed(text.split()):
        if estimate_tokens(" ".join([word, *words])) > max_tokens:
            break
        words.insert(0, word)
    return " ".join(words)


def _hard_split(sentence: str, *, max_tokens: int) -> list[str]:
    """한 문장이 너무 길면 글자 수로 자른다 (공백에서 자르려고 한다)."""
    pieces = []
    rest = sentence
    while rest:
        if estimate_tokens(rest) <= max_tokens:
            pieces.append(rest)
            break
        # 토큰 상한에 맞는 글자 수를 어림해 그 근처 공백에서 자른다.
        ratio = max_tokens / max(estimate_tokens(rest), 1)
        cut = max(1, int(len(rest) * ratio))
        space = rest.rfind(" ", 0, cut)
        cut = space if space > cut // 2 else cut
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [piece for piece in pieces if piece]


# ---------- 질의 ----------


def is_context_dependent(query: str) -> bool:
    """문서 없이는 가리키는 것을 알 수 없는 질의인지 ("이 글에서", "그는" …)."""
    return bool(_CONTEXT_DEPENDENT.search(query.strip()))


def query_type(query: str) -> str:
    """질의 유형: who(누가 · 무엇) · when_where(언제 · 어디) · why_how(왜 · 어떻게) · keyword(검색어형) · other."""
    text = query.strip()
    if _WHY_HOW.search(text):
        return "why_how"
    if _WHEN_WHERE.search(text):
        return "when_where"
    if _WHO.search(text):
        return "who"
    if not _QUESTION_END.search(text):
        return "keyword"
    return "other"


def _bigrams(text: str) -> set[str]:
    compact = "".join(unicodedata.normalize("NFC", text).split())
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def lexical_overlap(query: str, document: str) -> float:
    """질의의 글자 두 개 묶음 가운데 문서에도 있는 몫 (0~1)."""
    query_grams = _bigrams(query)
    if not query_grams:
        return 0.0
    document_grams = _bigrams(document)
    return len(query_grams & document_grams) / len(query_grams)


def best_chunk(chunks: list[str], *, query: str, answer: str | None) -> int:
    """판정을 옮길 청크의 자리: 답 근거가 든 청크, 없으면 질의와 글자가 가장 많이 겹치는 청크."""
    if answer:
        for index, chunk in enumerate(chunks):
            if answer in chunk:
                return index
    return max(range(len(chunks)), key=lambda index: lexical_overlap(query, chunks[index]))


def is_easy_pair(query: str, document: str) -> bool:
    """질의가 문서 문장을 거의 그대로 베낀 쌍인지 (글자 두 개 묶음 겹침 ≥ EASY_PAIR_OVERLAP)."""
    compact = "".join(query.split())
    if len(compact) < EASY_PAIR_MIN_CHARS:
        return False
    return lexical_overlap(query, document) >= EASY_PAIR_OVERLAP


# ---------- 같은 규칙의 SQL 모양 (PostgreSQL 정규식, 데이터 탭 거르기 · 진단의 수) ----------

# 문맥 의존 질의 (~ 로 쓴다)
SQL_CONTEXT_DEPENDENT = (
    r"^(그|그녀|그들|이들|이것|그것|저것)(는|은|이|가|의|를|을)?\s|"
    r"(이|위|아래|본|해당)\s*(글|기사|문서|내용|규정|조항|표|문단|지문)"
)

# 긴 질의로 보는 글자 수: 질의 최대 토큰 × 이 값 (한국어는 글자당 토큰이 1에 가깝다)
LONG_QUERY_CHARS_PER_TOKEN = 1.5
