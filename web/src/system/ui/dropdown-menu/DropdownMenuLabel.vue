<!-- 펼친 목록 위의 작은 제목 (shadcn-vue). 작은 대문자식 글자. -->
<script setup lang="ts">
import type { DropdownMenuLabelProps } from "reka-ui"
import type { HTMLAttributes } from "vue"
import { reactiveOmit } from "@vueuse/core"
import { DropdownMenuLabel, useForwardProps } from "reka-ui"
import { cn } from '@/system/ui/cn'

const props = defineProps<DropdownMenuLabelProps & { class?: HTMLAttributes["class"] }>()

const delegatedProps = reactiveOmit(props, "class")
const forwardedProps = useForwardProps(delegatedProps)
</script>

<template>
  <DropdownMenuLabel
    data-slot="dropdown-menu-label"
    v-bind="forwardedProps"
    :class="cn('caps px-2 pt-1.5 pb-1', props.class)"
  >
    <slot />
  </DropdownMenuLabel>
</template>
