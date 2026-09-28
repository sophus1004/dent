// 1rem이 몇 px인지 (브라우저 글꼴 크기 설정을 따른다). 좁은 모양 경계를 rem으로 두고 요소 폭(px)과 견줄 때 쓴다.

// 루트 글꼴 크기를 못 읽을 때의 1rem (px)
const FALLBACK_REM_PX = 16

/** 지금 1rem의 px. */
export function remPx(): number {
  return Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || FALLBACK_REM_PX
}
