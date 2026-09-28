// 사이드바를 아이콘 줄(rail)로 접는지. 창이 73.75rem(기본 글꼴에서 1180px) 이하면 접는다.
// 사이드바 폭(15.5rem · 접으면 3.75rem)과 이 경계는 rem이라 브라우저 확대 · 글꼴 크기를 따른다.
// 도우미 창은 이 순서에 맞춘다(system/helper/HelperPanel.vue): 사이드바가 펼쳐진 동안은 본문 옆에 둘 수 있고,
// 겹치기는 사이드바가 제 규칙대로 접힌 뒤에만 온다. 모양은 globals.css의 rail: 변형이 바꾸고(두 곳의 경계가 같아야 한다),
// 이 값은 접혔을 때만 툴팁을 켜는 데 쓴다.
import { useMediaQuery } from '@vueuse/core'

export const isRail = useMediaQuery('(max-width: 73.75rem)')
