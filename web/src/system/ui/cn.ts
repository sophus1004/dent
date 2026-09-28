// class 글자를 합친다. 같은 종류의 Tailwind class가 겹치면 뒤의 것이 이긴다. shadcn-vue 부품이 쓴다.
import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// globals.css의 글자 크기 이름. 알려 주지 않으면 text-meta를 글자색으로 알고 text-foreground 같은 색 class를 지운다.
const FONT_SIZES = ['caps', 'meta', 'ui', 'body', 'title', 'verdict', 'kpi']

const twMerge = extendTailwindMerge({ extend: { theme: { text: FONT_SIZES } } })

/** 여러 class를 하나로 합친다. cn('px-2', isOn && 'px-4') → 'px-4' */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
