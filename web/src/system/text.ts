// 같은 문장을 가리는 모양. 백엔드(src/dent/system/text.py의 normalize_text)와 같은 규칙이다.
// 유니코드 정규화(NFC)하고 공백을 하나로 줄인다. 그래서 띄어쓰기만 다른 문장도 같은 것으로 본다.

/** 같은 문장인지 비교할 때 쓰는 모양. normalizeText('a  b ') → 'a b' */
export function normalizeText(text: string): string {
  return text.normalize('NFC').split(/\s+/).filter(Boolean).join(' ')
}
