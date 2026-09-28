// 의미 지도의 색(모든 모듈). 이 색은 지도에서만 쓴다(다른 화면의 라벨 칩은 색 없이 둔다).
//   범주 색 8개: 밝게 · 어둡게 따로 고른 값. 순서가 색약 구분을 지키는 장치라서 바꾸지 않는다
//                (이웃한 색끼리 색약 모의 ΔE ≥ 8, 밝은 배경 대비가 3:1 아래인 색은 범례의 이름 · 수로 보완).
//   라벨이 8개를 넘으면 9번째부터는 '기타' 회색 하나로 묶는다(새 색을 지어내지 않는다).
//   문제는 앞의 여섯 색. 문제 없는 점은 흐린 회색이다. 점의 표시 비트는 모듈이 정한다.

// 밝게 · 어둡게
export type MapTheme = 'light' | 'dark'

// 범주 색 (순서 고정)
export const CATEGORY_COLORS: Record<MapTheme, string[]> = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
}

// 9번째부터의 라벨, 라벨이 없는 점
export const OTHER_COLOR: Record<MapTheme, string> = { light: '#8a8a97', dark: '#75757f' }

// 문제 없는 점 (문제로 칠할 때)
export const NEUTRAL_COLOR: Record<MapTheme, string> = { light: '#c4c4cd', dark: '#3a3a44' }

/** 지금 화면이 어두운 테마인지. <html data-theme>을 본다(theme.ts가 늘 light나 dark를 넣는다). */
export function currentMapTheme(): MapTheme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}
