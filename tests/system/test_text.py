"""글자 정규화 · 해시 테스트 (DB 없이)."""

import unicodedata

from dent.system.text import make_text_hash, normalize_text


def test_make_text_hash_ignores_spacing_and_unicode_composition():
    # 준비: 같은 글자를 완성형(NFC)과 풀어 쓴 조합형(NFD)으로
    composed = unicodedata.normalize("NFC", "한글")
    decomposed = unicodedata.normalize("NFD", "한글")
    assert composed != decomposed

    # 실행
    hashes = {make_text_hash(composed), make_text_hash(f"  {decomposed} "), make_text_hash("한글")}

    # 확인
    assert len(hashes) == 1
    assert make_text_hash("안녕  하세요") == make_text_hash("안녕 하세요")


def test_normalize_text_joins_spaces_and_composes_letters():
    # 실행
    normalized = normalize_text(unicodedata.normalize("NFD", "  안녕 \n 하세요\t"))

    # 확인
    assert normalized == unicodedata.normalize("NFC", "안녕 하세요")
