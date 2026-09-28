"""글자 정규화와 같은 문장을 찾는 열쇠(해시). 모듈 종류와 상관없이 쓴다.

같은 문장인지는 유니코드 정규화(NFC)하고 공백을 하나로 줄인 문장으로 가린다.
그래서 띄어쓰기나 글자 조합 방식만 다른 문장도 같은 것으로 본다.
같은 문장을 중복·충돌 가운데 무엇으로 볼지는 모듈이 정한다.
"""

import hashlib
import unicodedata

# 문장 해시 길이. sha256을 16진수로 쓰면 64자다.
TEXT_HASH_LENGTH = 64


def normalize_text(text: str) -> str:
    """같은 문장을 가릴 때 쓰는 모양: NFC로 정규화하고 공백을 하나로 줄인다."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def make_text_hash(text: str) -> str:
    """같은 문장을 찾는 열쇠: normalize_text한 문장의 sha256 16진수."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
