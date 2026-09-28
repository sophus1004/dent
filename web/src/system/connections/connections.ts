// 연결 설정 대화상자를 여닫는 곳. 대화상자는 App.vue에 하나만 있고, 어디서든 openConnectionSettings()로 연다
// (홈의 시스템 칸, 진단 탭의 잠긴 줄). 확인 결과를 한 줄 글자로 바꾸는 일도 여기서 한다.
import { ref } from 'vue'

import type { ConnectionCheckRead, LlmProvider } from '@/system/types'

// 대화상자가 열려 있는지
export const isConnectionSettingsOpen = ref(false)

/** 연결 설정 대화상자를 연다. */
export function openConnectionSettings(): void {
  isConnectionSettingsOpen.value = true
}

// 확인 결과 한 줄: '연결됨' · '실패' · (내장 모델이 준비되기 전) '내려받는 중' · '확인하는 중' · '불러오는 중' 낱말과 사실(또는 까닭)
export interface CheckLine {
  word: '연결됨' | '실패' | '내려받는 중' | '확인하는 중' | '불러오는 중'
  // 예: '1024차원 · 42ms', 'mps · english,multilingual · 25ms', '연결 거부', '25% · 0.5 / 2.0GB'
  rest: string
}

// 내장 모델 서버의 준비 단계(facts.phase) → 낱말
const PREPARING_WORDS: Record<string, CheckLine['word']> = {
  downloading: '내려받는 중',
  verifying: '확인하는 중',
  loading: '불러오는 중',
}

/** 확인 결과를 '연결됨 · 1024차원 · 42ms' / '실패 · 연결 거부' / '내려받는 중 · 25% · …' 모양의 두 조각으로. */
export function checkLine(check: ConnectionCheckRead): CheckLine {
  const phase = check.facts.phase
  const preparing = typeof phase === 'string' ? PREPARING_WORDS[phase] : undefined
  if (preparing) return { word: preparing, rest: check.detail }
  return { word: check.ok ? '연결됨' : '실패', rest: check.detail }
}



// http:// 또는 https://로 시작하고 뒤에 서버 이름이 있는지. 서버가 한 번 더 검사한다.
const ADDRESS_PATTERN = /^https?:\/\/[^\s/]+/i

/** 주소 모양이 맞는지. 비어 있으면 false */
export function isAddress(value: string): boolean {
  return ADDRESS_PATTERN.test(value.trim())
}

// LLM 공급자: 이름과 처음 주소. vLLM은 자기 서버라 주소가 정해져 있지 않다.
export const LLM_PROVIDERS: { value: LlmProvider; name: string; address: string }[] = [
  { value: 'openai', name: 'OpenAI', address: 'https://api.openai.com/v1' },
  { value: 'anthropic', name: 'Anthropic', address: 'https://api.anthropic.com/v1' },
  { value: 'google', name: 'Google', address: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  { value: 'xai', name: 'xAI', address: 'https://api.x.ai/v1' },
  { value: 'vllm', name: 'vLLM', address: '' },
]

/** 공급자의 처음 주소. 모르면 빈 글자 */
export function providerAddress(provider: LlmProvider | null): string {
  return LLM_PROVIDERS.find((item) => item.value === provider)?.address ?? ''
}
