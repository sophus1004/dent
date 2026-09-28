// 도우미 창이 열리고 닫힐 때의 움직임. 데이터셋 화면이 도우미 창을 <Transition :css="false" @enter @leave>로 감싸 건다.
//   옆에 붙은 창 · 겹칠 때의 자리(레일 칸): 폭이 0 ↔ 제 폭으로 바뀌어 본문이 함께 부드럽게 줄고 는다.
//   겹쳐 펼친 창: 오른쪽에서 밀려 들어오고 나간다.
// 사람이 열고 닫았을 때(helperSignals.setHelperOpen)만 움직인다. 데이터셋을 옮기며 창이 잠깐 내려갔다 올라올 때는 움직이지 않는다.
// 동작 줄이기 설정이면 움직이지 않는다.
import { helperToggledAt } from '@/system/helper/helperSignals'

// 열 때 · 닫을 때 걸리는 시간(ms)과 빠르기 곡선
const OPEN_MS = 220
const CLOSE_MS = 180
const EASE_OUT = 'cubic-bezier(0.2, 0, 0, 1)'
const EASE_IN = 'cubic-bezier(0.4, 0, 1, 1)'

// 사람이 누른 뒤 이 시간 안에 창이 붙거나 떨어지면 그 누름 때문으로 본다(ms)
const TOGGLE_WINDOW_MS = 600

function shouldMove(): boolean {
  const reducesMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  return !reducesMotion && Date.now() - helperToggledAt.value < TOGGLE_WINDOW_MS
}

function animate(root: Element, opening: boolean): Animation[] {
  const options: KeyframeAnimationOptions = {
    duration: opening ? OPEN_MS : CLOSE_MS,
    easing: opening ? EASE_OUT : EASE_IN,
    fill: 'forwards',
  }
  const running: Animation[] = []
  // 줄 안에서 자리를 차지하는 것: 옆에 붙은 창 · 겹칠 때의 자리
  const inRow = root.querySelectorAll<HTMLElement>('[data-helper-panel="beside"], [data-helper-rail]')
  for (const element of inRow) {
    const frames = [{ width: '0px' }, { width: `${element.getBoundingClientRect().width}px` }]
    running.push(element.animate(opening ? frames : [...frames].reverse(), options))
  }
  const floating = root.querySelector<HTMLElement>('[data-helper-panel="over"]')
  if (floating) {
    const frames = [
      { transform: 'translateX(100%)', opacity: 0 },
      { transform: 'translateX(0)', opacity: 1 },
    ]
    running.push(floating.animate(opening ? frames : [...frames].reverse(), options))
  }
  return running
}

function run(root: Element, opening: boolean, done: () => void): void {
  if (!shouldMove()) {
    done()
    return
  }
  // 한 번 누름에 한 번만 움직인다(바로 이어 데이터셋을 옮겨 창이 다시 붙어도 움직이지 않게).
  helperToggledAt.value = 0
  const running = animate(root, opening)
  void Promise.all(running.map((animation) => animation.finished)).then(
    () => {
      // 연 뒤에는 움직임을 떼어 창의 폭이 다시 스스로 바뀌게 한다(닫을 때는 곧 사라지므로 그대로 둔다).
      if (opening) running.forEach((animation) => animation.cancel())
      done()
    },
    done,
  )
}

/** 도우미 창이 붙을 때 (Transition @enter). */
export function helperEnter(root: Element, done: () => void): void {
  run(root, true, done)
}

/** 도우미 창이 떨어질 때 (Transition @leave). 움직임이 끝나야 창을 뗀다. */
export function helperLeave(root: Element, done: () => void): void {
  run(root, false, done)
}
