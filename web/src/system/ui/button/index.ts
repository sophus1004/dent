// 버튼 모양: 높이 32px, 13px 글자, 보라 주 버튼.
//   variant: default(보라 주 버튼) · outline(흰 바탕 테두리) · ghost(바탕 없음) · quiet(옅은 글자)
//   size: default 32px · lg 36px · sm 28px · xs 24px · icon 정사각형
import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export { default as Button } from "./Button.vue"

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent text-ui font-[550] leading-none select-none transition-[background-color,border-color,color,box-shadow] active:translate-y-px disabled:pointer-events-none disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-[15px] outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-[0_1px_2px_rgba(40,20,120,0.25),inset_0_1px_0_rgba(255,255,255,0.14)] hover:bg-primary-hover",
        outline:
          "border-border-strong bg-card text-foreground shadow-(--shadow-xs) hover:bg-muted",
        ghost:
          "text-foreground hover:bg-muted",
        quiet:
          "text-muted-foreground hover:bg-muted hover:text-foreground",
      },
      size: {
        "default": "h-8 px-3",
        "lg": "h-9 px-3.5 text-body",
        "sm": "h-7 gap-[5px] px-2.5 [&_svg:not([class*='size-'])]:size-3.5",
        "xs": "h-6 gap-1 rounded-sm px-2 text-meta [&_svg:not([class*='size-'])]:size-[13px]",
        "icon": "size-8",
        "icon-sm": "size-7 [&_svg:not([class*='size-'])]:size-3.5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
)
export type ButtonVariants = VariantProps<typeof buttonVariants>
