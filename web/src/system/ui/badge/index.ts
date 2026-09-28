// 칩 모양. 색 없이 글자 + 1px 테두리만 쓴다(라벨·형식 이름 같은 낱말 표시).
import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export { default as Badge } from "./Badge.vue"

export const badgeVariants = cva(
  "inline-flex h-6 w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2 text-ui font-medium [&>svg]:pointer-events-none [&>svg]:size-3.5",
  {
    variants: {
      variant: {
        outline: "bg-card text-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]",
      },
    },
    defaultVariants: {
      variant: "outline",
    },
  },
)
export type BadgeVariants = VariantProps<typeof badgeVariants>
