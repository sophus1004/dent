"""평가셋 오염 대조(모든 모듈): 학습 글이 평가 문항과 겹치는지 8-gram으로 잰다.

평가 문항의 낱말 가운데 학습 글과 같은 8-gram(낱말 8개 이어짐)에 드는 몫이 COVERAGE_LIMIT를 넘으면 겹친 것이다
(Tülu 3의 오염 검사와 같은 기준). 낱말은 정규화한 글(NFC · 소문자)의 글자 · 숫자 덩어리다(문장 부호는 뺀다).
문항이 8낱말보다 짧으면 낱말이 MIN_SHORT_WORDS 이상일 때만 그 낱말들이 학습 글에 그대로 이어져 있는지 본다
(짧은 인사 같은 문항이 아무 글에나 걸리지 않게).

평가셋은 사용자가 가져와 표시한다(평가셋 대부분은 다시 나눠 줄 수 없어 DENT에 넣지 않는다). 대조는 이 PC 안에서만 한다.
DB를 모르는 계산만 둔다. 문항을 모으고 결과를 적는 일은 모듈이 한다.
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from dent.system.text import normalize_text

# 낱말 몇 개를 이어 겹침을 보나
NGRAM = 8

# 평가 문항의 낱말 가운데 겹친 8-gram에 든 몫이 이보다 크면 겹침
COVERAGE_LIMIT = 0.5

# 8낱말보다 짧은 문항은 낱말이 이 수 이상일 때만 통째로 이어져 있는지 본다
# (한국어는 낱말이 길어 '대한민국의 수도는 어디입니까'도 3낱말이다)
MIN_SHORT_WORDS = 3

# n-gram 열쇠의 길이(sha256 앞 바이트). 겹칠 확률은 무시할 만하고 메모리를 아낀다.
GRAM_KEY_BYTES = 8


@dataclass(frozen=True)
class Match:
    """학습 글 하나가 가장 많이 겹친 평가 문항."""

    # 평가 문항 번호 (모듈이 준 번호)
    eval_id: int

    # 평가 문항의 낱말 가운데 겹친 몫 (0~1)
    coverage: float


@dataclass
class _Item:
    """평가 문항 하나의 낱말 수와 8-gram 열쇠들(앞에서부터)."""

    words: int
    grams: list[bytes] = field(default_factory=list)
    short: str | None = None


# 낱말: 글자 · 숫자 덩어리 (문장 부호 · 기호는 낱말을 가른다)
WORD = re.compile(r"\w+")


def words_of(text: str) -> list[str]:
    """대조에 쓰는 낱말들 (정규화 · 소문자 · 글자와 숫자 덩어리)."""
    return WORD.findall(normalize_text(text).lower())


class EvalIndex:
    """평가 문항들의 8-gram 색인. 학습 글마다 가장 많이 겹친 문항을 찾는다."""

    def __init__(self, items: Iterable[tuple[int, str]]) -> None:
        self._items: dict[int, _Item] = {}
        self._by_gram: dict[bytes, list[int]] = {}
        self._short: list[tuple[int, str]] = []
        for eval_id, text in items:
            words = words_of(text)
            if len(words) >= NGRAM:
                grams = [_gram(words[i : i + NGRAM]) for i in range(len(words) - NGRAM + 1)]
                self._items[eval_id] = _Item(words=len(words), grams=grams)
                for gram in set(grams):
                    self._by_gram.setdefault(gram, []).append(eval_id)
            elif len(words) >= MIN_SHORT_WORDS:
                self._short.append((eval_id, f" {' '.join(words)} "))

    def __len__(self) -> int:
        return len(self._items) + len(self._short)

    def best_match(self, text: str) -> Match | None:
        """학습 글이 COVERAGE_LIMIT를 넘게 겹친 평가 문항 가운데 가장 많이 겹친 것. 없으면 None."""
        words = words_of(text)
        best: Match | None = None
        if len(words) >= NGRAM:
            grams = {_gram(words[i : i + NGRAM]) for i in range(len(words) - NGRAM + 1)}
            candidates = {eval_id for gram in grams for eval_id in self._by_gram.get(gram, ())}
            for eval_id in sorted(candidates):
                coverage = _coverage(self._items[eval_id], grams)
                if coverage > COVERAGE_LIMIT and (best is None or coverage > best.coverage):
                    best = Match(eval_id=eval_id, coverage=coverage)
        if best is None and self._short:
            padded = f" {' '.join(words)} "
            for eval_id, short in self._short:
                if short in padded:
                    return Match(eval_id=eval_id, coverage=1.0)
        return best


def _gram(words: list[str]) -> bytes:
    return hashlib.sha256(" ".join(words).encode("utf-8")).digest()[:GRAM_KEY_BYTES]


def _coverage(item: _Item, grams: set[bytes]) -> float:
    """문항의 낱말 가운데 학습 글과 같은 8-gram에 든 몫."""
    covered = [False] * item.words
    for start, gram in enumerate(item.grams):
        if gram in grams:
            for position in range(start, start + NGRAM):
                covered[position] = True
    return sum(covered) / item.words
