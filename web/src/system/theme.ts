// 테마: 시스템 → 밝게 → 어둡게 순서로 돈다. 고른 것은 이 브라우저에 기억한다.
// 화면에는 늘 밝게·어둡게 중 하나를 <html data-theme>에 넣는다. 시스템이면 운영체제 설정을 따라 넣는다.
// 첫 화면을 그리기 전의 적용은 index.html의 짧은 스크립트가 같은 방법으로 한다.
import { ref } from 'vue'

export type Theme = 'system' | 'light' | 'dark'

// 누를 때마다 도는 순서
export const THEMES: Theme[] = ['system', 'light', 'dark']

// 화면에 보일 이름
export const THEME_NAMES: Record<Theme, string> = { system: '시스템', light: '밝게', dark: '어둡게' }

// 고른 테마를 기억하는 저장소 열쇠. index.html의 스크립트와 같은 이름이다.
const STORAGE_KEY = 'dent-theme'

// 운영체제가 어두운 테마인지 알려 주는 조건
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')

// 지금 고른 테마
export const theme = ref<Theme>(readSavedTheme())

/** 누르면 바뀔 다음 테마. 시스템 → 밝게 → 어둡게 → 시스템 */
export function nextTheme(): Theme {
  return THEMES[(THEMES.indexOf(theme.value) + 1) % THEMES.length]
}

/** 다음 테마로 바꾸고 기억한다. */
export function cycleTheme(): void {
  const next = nextTheme()
  theme.value = next
  applyTheme()
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // 저장소를 못 쓰면 이번 화면에만 적용한다.
  }
}

/** 고른 테마를 <html data-theme>에 넣는다. 시스템이면 운영체제 설정을 따른다. */
export function applyTheme(): void {
  const isDark = theme.value === 'dark' || (theme.value === 'system' && systemDark.matches)
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
}

// 시스템을 고른 채 운영체제 테마가 바뀌면 따라간다.
systemDark.addEventListener('change', applyTheme)

function readSavedTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    const isKnown = THEMES.includes(saved as Theme)
    return isKnown ? (saved as Theme) : 'system'
  } catch {
    return 'system'
  }
}
