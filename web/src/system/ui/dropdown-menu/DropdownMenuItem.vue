<!-- 펼친 목록의 한 줄 (shadcn-vue). 고르면 @select가 불린다. 글쇠(↑↓)로 옮기면 회색 바탕이 켜진다. -->
<script setup lang="ts">
import type { DropdownMenuItemProps } from "reka-ui"
import type { HTMLAttributes } from "vue"
import { reactiveOmit } from "@vueuse/core"
import { DropdownMenuItem, useForwardProps } from "reka-ui"
import { cn } from '@/system/ui/cn'

const props = defineProps<DropdownMenuItemProps & { class?: HTMLAttributes["class"] }>()

const delegatedProps = reactiveOmit(props, "class")
const forwardedProps = useForwardProps(delegatedProps)
</script>

<template>
  <DropdownMenuItem
    data-slot="dropdown-menu-item"
    v-bind="forwardedProps"
    :class="cn('relative flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-ui outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*=size-])]:size-[15px]', props.class)"
  >
    <slot />
  </DropdownMenuItem>
</template>
