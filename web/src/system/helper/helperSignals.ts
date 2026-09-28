// 도우미 창과 다른 화면을 잇는 작은 신호(모든 모듈). 도우미 창이 열렸는지(⌘J) · 겹친 창을 레일로 접었는지,
// 도우미가 데이터를 바꿨는지.
// 데이터를 바꾸면(바꾼 카드 · 결과 · 되돌리기) 진단 탭과 데이터셋 머리가 이 값을 보고 다시 읽는다.
import { ref } from 'vue'

// 창이 열렸는지. 데이터셋을 옮겨도 열린 채로 둔다(브라우저에 기억).
const OPEN_KEY = 'dent-helper-open'

// 겹친 창을 레일로 접었는지 (본문 옆에 둘 자리가 없어 겹칠 때만 쓴다. 브라우저에 기억)
const FOLDED_KEY = 'dent-helper-folded'

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeFlag(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    // 기억하지 못해도 이번 화면에서는 바뀐다.
  }
}

export const isHelperOpen = ref(readFlag(OPEN_KEY))
export const isHelperFolded = ref(readFlag(FOLDED_KEY))

// 사람이 도우미 창을 마지막으로 열거나 닫은 시각(ms). 여닫는 움직임은 이때만 한다(helperMotion.ts).
export const helperToggledAt = ref(0)

/** 도우미 창을 열고 닫는다. 열면 늘 펼친 채로 연다(겹칠 때 레일로 접혀 있었어도). */
export function setHelperOpen(open: boolean): void {
  if (open !== isHelperOpen.value) helperToggledAt.value = Date.now()
  isHelperOpen.value = open
  writeFlag(OPEN_KEY, open)
  if (open) setHelperFolded(false)
}

/** 겹친 도우미 창을 레일로 접거나 편다. */
export function setHelperFolded(folded: boolean): void {
  isHelperFolded.value = folded
  writeFlag(FOLDED_KEY, folded)
}

// 도우미 창이 지금 본문 위에 겹쳐 있는지 (옆에 둘 자리가 없을 때). 도우미 창(HelperPanel)이 적는다.
export const isHelperOverlaid = ref(false)

/** 상단 바 [도우미] · ⌘J: 겹쳐서 레일로 접혀 있으면 펴고, 아니면 열고 닫는다. */
export function toggleHelper(): void {
  const isRailOnly = isHelperOpen.value && isHelperOverlaid.value && isHelperFolded.value
  if (isRailOnly) {
    setHelperFolded(false)
    return
  }
  setHelperOpen(!isHelperOpen.value)
}

// 도우미가 데이터를 바꿀 때마다 1씩 는다
export const helperChanged = ref(0)

/** 도우미가 데이터를 바꿨다(또는 되돌렸다). */
export function notifyHelperChanged(): void {
  helperChanged.value += 1
}
