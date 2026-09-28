<!-- 대화상자 머리 (shadcn-vue). 제목과 설명을 묶는다. -->
<script setup lang="ts">
import type { HTMLAttributes } from "vue"
import { cn } from '@/system/ui/cn'

const props = defineProps<{
  class?: HTMLAttributes["class"]
}>()
</script>

<template>
  <div
    data-slot="dialog-header"
    :class="cn('flex flex-col gap-2 text-center sm:text-left', props.class)"
  >
    <slot />
  </div>
</template>
