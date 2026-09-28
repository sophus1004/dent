<!-- 불러오는 동안 자리를 채우는 회색 막대 (shadcn-vue). -->
<script setup lang="ts">
import type { HTMLAttributes } from "vue"
import { cn } from '@/system/ui/cn'

interface SkeletonProps {
  class?: HTMLAttributes["class"]
}

const props = defineProps<SkeletonProps>()
</script>

<template>
  <div
    data-slot="skeleton"
    :class="cn('animate-pulse rounded-[5px] bg-muted', props.class)"
  />
</template>
