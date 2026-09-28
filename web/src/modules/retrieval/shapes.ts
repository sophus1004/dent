// 원본 모양 6장(가져오기 3단계)의 낱말: 이름 · 아이콘 · 칸 · 예 · 흐름의 입구 · 채울 것. 백엔드 models.ENTRY_STAGE와 입구가 같다.
import { ArrowRightLeft, BookOpenText, FileText, Files, ListOrdered, Rows3, type LucideIcon } from '@lucide/vue'

import type { Shape } from '@/modules/retrieval/types'

export interface ShapeWords {
  name: string
  icon: LucideIcon
  // 카드에 보일 칸 이름
  columns: string[]
  // 예
  example: string
  // 흐름의 입구 단계
  entry: number
  // 입구 뒤 채울 것 [이름, 허락이 필요한지]
  fills: [string, boolean][]
}

export const SHAPES: Record<Shape, ShapeWords> = {
  pair: {
    name: '쌍',
    icon: ArrowRightLeft,
    columns: ['질의', '정답'],
    example: 'FAQ · 검색 기록',
    entry: 2,
    fills: [['오답 찾기', false]],
  },
  triplet: {
    name: '세 쌍',
    icon: Rows3,
    columns: ['질의', '정답', '오답 …'],
    example: 'bge · sentence-transformers',
    entry: 3,
    fills: [['거짓 오답 확인', false], ['모자라면 오답 찾기', false]],
  },
  mrc: {
    name: '독해 MRC',
    icon: BookOpenText,
    columns: ['질문', '지문', '답'],
    example: 'KorQuAD · KLUE-MRC',
    entry: 2,
    fills: [['오답 찾기', false]],
  },
  scored: {
    name: '점수',
    icon: ListOrdered,
    columns: ['질의', '문서', '점수'],
    example: '리랭커 · 사람 등급',
    entry: 3,
    fills: [['거짓 오답 확인', false]],
  },
  documents: {
    name: '문서만',
    icon: FileText,
    columns: ['본문', '제목'],
    example: '규정 · 매뉴얼 · 기사',
    entry: 1,
    fills: [['나누기', true], ['질의 만들기', true], ['오답 찾기', false]],
  },
}

// 카드 순서
export const SHAPE_ORDER: Shape[] = ['pair', 'triplet', 'mrc', 'scored', 'documents']

// 아직 없는 모양 (카드만 흐리게 보인다)
export const SOON_SHAPE = { name: 'BEIR', icon: Files, columns: ['corpus', 'queries', 'qrels'], example: '세 파일' }
