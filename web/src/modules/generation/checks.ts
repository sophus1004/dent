// 생성 검사의 낱말(이름 · 아이콘 · 정의 · 영향 · 조치), 결정 · 고치는 쪽의 낱말, 서버의 검사 한 줄(CheckRead)을 표 한 줄(Check)로 바꾸기.
// 등급 · 수는 서버(overview.py)가 정한다. 화면은 낱말 · 등급 사다리 글 · 검토로 가는 주소만 붙인다.
// 진단 · 검토 · 데이터 탭의 문제 칩이 모두 이 낱말을 쓴다(검사 이름 = 문제 이름).
import {
  Ban,
  ChartBar,
  ChartPie,
  Code,
  Copy,
  CopySlash,
  FileWarning,
  FlaskConical,
  IdCard,
  Languages,
  ListTree,
  MessageSquareDashed,
  MessageSquareOff,
  MessageSquareWarning,
  MoveHorizontal,
  Repeat,
  Repeat2,
  Ruler,
  Scale,
  Scissors,
  User,
  WandSparkles,
  type LucideIcon,
} from '@lucide/vue'

import type { Check, CheckWords, LadderStep } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'

import type { Action, CheckKey, CheckRead, Who } from '@/modules/generation/types'

export const CHECK_WORDS: Record<CheckKey, CheckWords> = {
  // 1 지시
  structure: {
    name: '대화 구조',
    icon: ListTree,
    definition: '역할 순서가 틀린 대화 · system은 맨 앞에 하나 · user와 응답이 번갈아 · 끝은 응답',
    impact: '학습 중단 · 형식 오류',
    fix: '빼기',
  },
  empty_prompt: {
    name: '빈 지시',
    icon: MessageSquareDashed,
    definition: '사용자 말이 빈 대화',
    impact: '학습 신호 없음',
    fix: '빼기',
  },
  duplicate: {
    name: '같은 지시',
    icon: Copy,
    definition: '맥락(system · 앞 턴 · 마지막 질문)이 같은 대화의 여분 · 번호가 가장 작은 것은 남김',
    impact: '학습 낭비',
    fix: '여분 빼기',
  },
  pii: {
    name: '개인정보',
    icon: IdCard,
    definition: '주민번호 · 휴대전화 · 카드 · 이메일 · API 키 모양',
    impact: '유출 위험',
    fix: '가리기',
  },
  // 2 응답
  empty_answer: {
    name: '빈 응답',
    icon: MessageSquareOff,
    definition: '응답 글이 빈 대화',
    impact: '학습 신호 없음',
    fix: '빼기',
  },
  truncated: {
    name: '잘린 응답',
    icon: Scissors,
    definition: '문장이 끝나지 않고 멈춘 응답 · 짧은 마지막 줄(서명 · 목록)은 뺌',
    impact: '끝맺음 못 배움',
    fix: '빼기 · 다시 만들기',
  },
  code_translated: {
    name: '번역된 코드',
    icon: Code,
    definition: '코드 안의 이름 · 문법이 한국어로 바뀐 응답 · 문자열 안의 한국어는 뺌',
    impact: '틀린 코드 학습',
    fix: '빼기 · 다시 쓰기',
  },
  loop: {
    name: '반복 루프',
    icon: Repeat,
    definition: '같은 조각이 다섯 번 넘게 잇달아 나오는 응답',
    impact: '반복 생성 학습',
    fix: '루프 앞에서 자르기',
  },
  refusal: {
    name: '거절 · 상투구',
    icon: Ban,
    definition: '거절 · "AI 언어 모델로서" 같은 상투구 · 다른 모델 이름',
    impact: '과한 거절 학습',
    fix: '정당하면 그대로',
  },
  short: {
    name: '짧은 응답',
    icon: Ruler,
    definition: '5자 미만 응답 · 분류 과제의 0 · 1처럼 맞는 답일 수 있음',
    impact: '근거 부족',
    fix: '맞으면 그대로',
  },
  repeat: {
    name: '반복 문장',
    icon: Repeat2,
    definition: '한 응답에 같은 긴 문장이 세 번 넘게 · 인용 · 절차일 수 있음',
    impact: '학습 낭비',
    fix: '사람이 확인',
  },
  lang_mix: {
    name: '언어 섞임',
    icon: Languages,
    definition: '코드 밖 글이 영어이거나 한자 · 가나가 섞인 응답',
    impact: '언어 흐림',
    fix: '빼기 · 다시 쓰기',
  },
  broken: {
    name: '깨진 글자',
    icon: FileWarning,
    definition: '대체 문자(�) · 아랍 · 키릴 · 타이 글자 · 예시 단어일 수 있음',
    impact: '잡음',
    fix: '사람이 확인',
  },
  long: {
    name: '긴 응답',
    icon: MoveHorizontal,
    definition: '6,000자 넘는 응답 · 흔한 최대 길이(4,096토큰)에서 뒤가 잘림 · 글자 수 어림',
    impact: '뒤가 잘려 학습',
    fix: '빼기',
  },
  // 뜻 분석이 재는 검사
  near_duplicate: {
    name: '근접 중복',
    icon: CopySlash,
    definition: '글은 달라도 뜻이 거의 같은 지시의 여분 · 지시 임베딩 코사인 ≥ 0.92 · 번호가 작은 것은 남김',
    impact: '학습 낭비',
    fix: '여분 빼기',
  },
  task_skew: {
    name: '과제 쏠림',
    icon: ChartPie,
    definition: '지시를 뜻으로 8무리로 나눴을 때 가장 큰 무리의 몫 · 코딩만 · 인사만처럼 좁게 배움',
    impact: '다른 과제 약함',
    fix: '데이터 더하기',
  },
  mismatch: {
    name: '지시 불일치',
    icon: MessageSquareWarning,
    definition: '지시를 따르지 않은 응답 · 지시와 응답의 뜻이 먼 것을 Jev에 물어 따름 예 ≤ 0.2',
    impact: '틀린 답 학습',
    fix: '빼기 · 다시 쓰기',
  },
}

// 결정의 낱말: 버튼 이름 · 설명(짧게) · 아이콘
export const ACTION_WORDS: Record<Action, { name: string; note: string }> = {
  drop: { name: '대화 빼기', note: '휴지통으로 · 되돌릴 수 있음' },
  keep: { name: '그대로 두기', note: '문제 아님 · 이 검사에서 다시 묻지 않음' },
  mask: { name: '가리기', note: '[이메일] · [전화번호]처럼' },
  cut: { name: '루프 앞에서 자르기', note: '첫 조각 하나만 남김' },
  rewrite: { name: 'LLM으로 다시 쓰기', note: '응답만 · 출처 합성 · 되돌릴 수 있음' },
}

// 결정 기록의 낱말 (한 뒤의 모양)
export const ACTION_DONE_WORDS: Record<Action, string> = {
  drop: '뺌',
  keep: '그대로 둠',
  mask: '가림',
  cut: '자름',
  rewrite: '다시 씀',
}

// 결정마다 하는 쪽: 빼기 · 가리기 · 자르기는 규칙대로, 그대로 두기는 사람의 판단, 다시 쓰기는 LLM
export const ACTION_WHO: Record<Action, Who> = {
  drop: 'rule',
  keep: 'human',
  mask: 'rule',
  cut: 'rule',
  rewrite: 'llm',
}

// 출처의 낱말
export const SOURCE_WORDS: Record<string, string> = {
  original: '원본',
  synthetic: '합성',
  human: '사람',
}

// 고치는 쪽의 낱말
export const WHO_WORDS: Record<Who, { name: string; icon: LucideIcon; tone: string }> = {
  rule: { name: '규칙', icon: Ruler, tone: 'bg-[color-mix(in_oklab,var(--success)_12%,var(--card))] text-success-ink' },
  jev: { name: 'Jev', icon: Scale, tone: 'bg-accent text-accent-foreground' },
  llm: { name: 'LLM', icon: WandSparkles, tone: 'bg-[color-mix(in_oklab,var(--warning)_14%,var(--card))] text-warning-ink' },
  human: { name: '사람', icon: User, tone: 'bg-muted text-foreground' },
}

// 아직 없는 뜻 검사 (다음 판: 평가셋을 가져와 대조한다). 진단에 흐린 줄(—)로만 선다.
export const PENDING_WORDS: Record<string, CheckWords> = {
  contamination: {
    name: '평가셋 오염',
    icon: FlaskConical,
    definition: '공개 평가셋(KMMLU · HAE-RAE …) 문항과 겹치는 지시',
    impact: '점수 부풀림',
    fix: '빼기',
  },
}

// 흐름 단계마다 늘 흐린 줄로 세울 검사 (서버가 아직 재지 않는 것)
export const PENDING_BY_STAGE: Record<number, string[]> = {
  1: ['contamination'],
  2: [],
}

// 뜻 분석이 재는 검사 (진단의 뜻 검사 카드)
export const SEMANTIC_KEYS: CheckKey[] = ['near_duplicate', 'task_skew', 'mismatch']

// 한 대화의 문제가 아니라 데이터셋의 모양인 검사 (검토 · 거르기가 없다)
export const SHAPE_KEYS: CheckKey[] = ['task_skew']

const UNKNOWN_WORDS: CheckWords = {
  name: '검사',
  icon: ChartBar,
  definition: '',
  impact: '',
  fix: '',
}

/** 검사 이름의 낱말. 모르는 검사면 이름만 key로. */
export function checkWords(key: string): CheckWords {
  return CHECK_WORDS[key as CheckKey] ?? PENDING_WORDS[key] ?? { ...UNKNOWN_WORDS, name: key }
}

/** 퍼센트 글: 0.0123 → '1.2%' · 경계는 '1%'. */
function percent(rate: number, digits = 1): string {
  return `${(rate * 100).toFixed(digits).replace(/\.0$/, '')}%`
}

/** 등급 사다리: 서버가 준 방식 · 경계를 글로. */
export function ladder(read: CheckRead): LadderStep[] {
  if (read.kind === 'any') return [{ grade: 'good', text: '0건' }, { grade: 'bad', text: '1건 이상' }]
  if (read.kind === 'warn_any') return [{ grade: 'good', text: '0건' }, { grade: 'warn', text: '1건 이상' }]
  if (read.kind === 'share') {
    return [
      { grade: 'good', text: `< ${percent(read.good_max, 0)}` },
      { grade: 'warn', text: `≥ ${percent(read.good_max, 0)}` },
    ]
  }
  return [
    { grade: 'good', text: `< ${percent(read.good_max, 0)}` },
    { grade: 'warn', text: `< ${percent(read.warn_max, 0)}` },
    { grade: 'bad', text: `≥ ${percent(read.warn_max, 0)}` },
  ]
}

/** 서버의 검사 한 줄 → 검사 표 한 줄. [→]는 그 검사의 검토 큐로 간다. */
export function toCheck(read: CheckRead, datasetId: number, conversations: number): Check {
  const words = checkWords(read.key)
  if (read.kind === 'share') {
    // 데이터셋의 모양: 가장 큰 무리의 몫. 볼 대화가 따로 없다(의미 지도에서 무리로 본다).
    return {
      key: read.key,
      grade: read.grade,
      value: String(Math.round(read.rate * 100)),
      unit: '%',
      sub: `가장 큰 무리 ${fmt(read.count)}건`,
      ladder: ladder(read),
      definition: words.definition,
      scope: ['분모', '뜻 분석한 대화'],
      view: null,
    }
  }
  return {
    key: read.key,
    grade: read.grade,
    value: fmt(read.count),
    unit: '건',
    sub: read.kind === 'rate' ? `${percent(read.rate)} · 대화 ${fmt(conversations)}` : '',
    ladder: ladder(read),
    definition: words.definition,
    scope: ['분모', `휴지통 밖 대화 ${fmt(conversations)}`],
    view:
      read.count > 0
        ? {
            to: { name: 'generation-review', params: { datasetId }, query: { check: read.key } },
            count: read.count,
            label: `검토 ${fmt(read.count)}건`,
          }
        : null,
  }
}
