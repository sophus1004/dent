// 검색 진단 검사의 낱말(이름 · 아이콘 · 정의 · 영향 · 조치)과, 서버의 검사 한 줄(CheckRead)을 표 한 줄(Check)로 바꾸기.
// 등급 · 값 · 사다리는 서버(overview.py)가 정한다. 화면은 낱말과 '볼 데이터'로 가는 주소만 붙인다.
import {
  ChartBar,
  ChartPie,
  CircleSlash,
  Copy,
  Equal,
  Eraser,
  Feather,
  GitCompare,
  Layers,
  ListFilter,
  MessageCircleQuestion,
  Ruler,
  ScanSearch,
  ShieldAlert,
  Stamp,
  CirclePlus,
  WrapText,
} from '@lucide/vue'
import type { RouteLocationRaw } from 'vue-router'

import type { Check, CheckWords } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'

import type { CheckRead } from '@/modules/retrieval/types'

export const CHECK_WORDS: Record<string, CheckWords> = {
  // 1 문서
  broken: {
    name: '글자 정리',
    icon: Eraser,
    definition: '깨진 글자(�) · 조절 문자 · HTML 찌꺼기가 든 문서',
    impact: '임베딩 흐림',
    fix: '정리',
  },
  repeat: {
    name: '반복 구간',
    icon: Stamp,
    definition: '문서의 0.5% 넘게 되풀이되는 문장 · 메타 모양(이메일 · 저작권 표시 …). 값은 떼기 · 남김을 아직 고르지 않은 틀 수',
    impact: '문서끼리 닮음',
    fix: '떼기 · 남김 고르기',
  },
  long: {
    name: '긴 문서',
    icon: WrapText,
    definition: '문서 최대 토큰(설정)을 넘는 문서. 학습 때 뒤가 잘린다',
    impact: '뒤가 잘림',
    fix: '나누기 · 허락',
  },
  duplicate_document: {
    name: '중복 문서',
    icon: Copy,
    definition: '학습 글이 같거나(해시) 뜻이 거의 같은(코사인 ≥ 0.95) 문서',
    impact: '거짓 오답 원인',
    fix: '합치기',
  },
  pick: {
    name: '질의 안 만들 청크',
    icon: ListFilter,
    definition: '목차 · 표만 · 너무 짧은 청크 · 반복 구간이 대부분인 청크. 질의를 만들 거리가 없다',
    impact: '쓸모없는 질의',
    fix: '만들기에서 빼기',
  },
  topic_skew: {
    name: '주제 쏠림',
    icon: ChartPie,
    definition: '문서 무리(KMeans 8) 가운데 가장 큰 무리의 몫',
    impact: '한쪽만 배움',
    fix: '얇은 무리에 질의 더',
  },
  // 2 질의
  no_queries: {
    name: '질의 없음',
    icon: MessageCircleQuestion,
    definition: '질의가 하나도 없다. 문서로 질의를 만든다',
    impact: '학습에 못 씀',
    fix: '질의 만들기 · 허락',
  },
  short_long: {
    name: '짧은 · 긴 질의',
    icon: Ruler,
    definition: '5자 미만이거나 질의 최대 토큰(설정)을 넘는 질의',
    impact: '근거 부족',
    fix: '검토 후 빼기',
  },
  conflict: {
    name: '판정 충돌',
    icon: GitCompare,
    definition: '같은 (질의, 문서)가 원본에서 정답이자 오답으로 나온 쌍',
    impact: '학습 신호 충돌',
    fix: '하나로',
  },
  duplicate_query: {
    name: '질의 중복',
    icon: Copy,
    definition: '정규화한 글이 같은 질의',
    impact: '한쪽으로 쏠림',
    fix: '하나만 남기기',
  },
  no_positive: {
    name: '정답 없음',
    icon: CircleSlash,
    definition: '정답 판정(등급 1 이상)이 하나도 없는 질의',
    impact: '학습에 못 씀',
    fix: '정답 찾기 · 빼기',
  },
  suspect: {
    name: '정답 의심',
    icon: ScanSearch,
    definition: '정답인데 기준 검색 100위 밖이고 Jev가 아니오라고 한 쌍',
    impact: '학습 신호 흐림',
    fix: '정답 떼기',
  },
  context: {
    name: '문맥 의존 질의',
    icon: MessageCircleQuestion,
    definition: "'이 글' · '위 문서' · '그는'처럼 문서를 봐야 뜻이 서는 질의",
    impact: '답이 여럿',
    fix: '질의 고치기',
  },
  easy_pair: {
    name: '쉬운 쌍',
    icon: Equal,
    definition: '질의 글자의 90% 넘게 정답 문서에 그대로 든 쌍. 상한(설정)까지는 둔다',
    impact: '일반화 약함',
    fix: '상한 넘은 만큼 바꿔 쓰기',
  },
  missing: {
    name: '빠진 정답',
    icon: CirclePlus,
    definition: '판정 없는 상위 문서를 Jev가 답을 담았다고 본 쌍. 오답 찾기 전에 붙인다',
    impact: '오답으로 뽑힘',
    fix: '정답으로 붙이기',
  },
  query_types: {
    name: '질의 유형',
    icon: ChartBar,
    definition: '검색어형(물음이 아닌 낱말 나열) 질의의 몫',
    impact: '실제 검색과 다름',
    fix: '검색어형 더',
  },
  // 3 하드 네거티브
  false_negative: {
    name: '거짓 오답',
    icon: ShieldAlert,
    definition: '오답인데 정답 유사도의 95%를 넘고 Jev가 답을 담았다고 본 쌍',
    impact: '정답을 밀어냄',
    fix: '정답으로 · 빼기',
  },
  same_negative: {
    name: '정답과 같은 오답',
    icon: Equal,
    definition: '정답 문서와 같은 중복 묶음(본문이 같거나 근접 중복)인 오답',
    impact: '정답을 밀어냄',
    fix: '빼기',
  },
  easy_negative: {
    name: '쉬운 오답',
    icon: Feather,
    definition: '기준 검색 100위 밖의 오답. 너무 쉬워 배울 것이 없다',
    impact: '학습 신호 약함',
    fix: '하드 오답으로',
  },
  no_negative: {
    name: '오답 수',
    icon: Layers,
    definition: '오답이 목표(설정, 기본 7 = group 8)보다 적은 질의. 오답 찾기는 풀(기본 20)까지 모은다',
    impact: '학습에서 빠짐',
    fix: '오답 찾기',
  },
  // 내보내기 창의 남은 주의 (검사가 아닌 것)
  teacher_no_jev: {
    name: '교사 점수 · Jev 미연결',
    icon: Stamp,
    definition: '교사 점수를 넣으려면 Jev가 있어야 한다',
    impact: '파일 못 만듦',
    fix: 'Jev 연결 · 교사 점수 끄기',
  },
  shared_positive: {
    name: '정답 같이 쓰는 질의',
    icon: Copy,
    definition: '같은 정답 문서를 쓰는 학습 질의. 한 배치에 들면 서로의 정답이 오답이 된다',
    impact: '거짓 오답',
    fix: 'GIST 손실 · 질의 줄이기',
  },
}

// 낱말이 없는 검사(서버가 새로 더한 것)의 기본 낱말
const UNKNOWN_WORDS: CheckWords = {
  name: '검사',
  icon: ChartBar,
  definition: '',
  impact: '',
  fix: '',
}

/** 검사 이름의 낱말. 모르는 검사면 이름만 key로. */
export function checkWords(key: string): CheckWords {
  return CHECK_WORDS[key] ?? { ...UNKNOWN_WORDS, name: key }
}

// 볼 데이터의 단위 낱말
const TARGET_NOUN = { queries: '질의', documents: '문서', suggestions: '제안' } as const

/** 서버의 검사 한 줄 → 검사 표의 한 줄. → 버튼은 데이터 탭(질의 · 문서)이나 제안 탭으로 간다. */
export function toCheck(read: CheckRead, datasetId: number): Check {
  const words = checkWords(read.key)
  return {
    key: read.key,
    grade: read.grade,
    value: read.value,
    unit: read.unit,
    sub: read.sub,
    ladder: read.ladder,
    definition: words.definition,
    scope: null,
    view: repeatView(read, datasetId) ?? (read.view_target && read.view_count > 0 ? checkView(read, datasetId) : null),
  }
}

/** 반복 구간 검사는 데이터 탭이 아니라 진단 위에 여는 고르기 창(DatasetLayout의 ?repeats=1)으로 간다. 고른 뒤에도 다시 연다. */
function repeatView(read: CheckRead, datasetId: number): Check['view'] {
  if (read.key !== 'repeat') return null
  return {
    to: { name: 'retrieval-diagnosis', params: { datasetId }, query: { repeats: '1' } },
    count: Number(read.value.replace(/,/g, '')) || 0,
    label: '반복 구간 고르기',
  }
}

function checkView(read: CheckRead, datasetId: number): Check['view'] {
  const target = read.view_target!
  let to: RouteLocationRaw
  if (target === 'suggestions') {
    to = { name: 'retrieval-suggestions', params: { datasetId }, query: { kind: read.view_problem ?? undefined } }
  } else {
    // 질의 검사는 학습에 쓰는 질의로 센다. 목록도 같은 상태로 걸러 수가 맞게 한다.
    to = {
      name: 'retrieval-data',
      params: { datasetId },
      query: {
        view: target,
        problem: read.view_problem ?? undefined,
        status: target === 'queries' ? 'included' : undefined,
      },
    }
  }
  return { to, count: read.view_count, label: `${TARGET_NOUN[target]} ${fmt(read.view_count)}건 보기` }
}
