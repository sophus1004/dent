<!--
  질의 · 문서의 상태 아이콘: 학습 포함(없음) · 학습 제외(눈 가림) · 휴지통.
  올리면 낱말이 뜬다.
-->
<script setup lang="ts">
import { EyeOff, Trash2 } from '@lucide/vue'
import { computed } from 'vue'

import { EXCLUDE_REASON_NAMES } from '@/modules/retrieval/data/filters'
import type { ExcludeReason } from '@/modules/retrieval/types'

const props = defineProps<{
  trashed: boolean
  excludeReason?: ExcludeReason | null
}>()

const state = computed(() => {
  if (props.trashed) return { icon: Trash2, text: '휴지통' }
  if (props.excludeReason) return { icon: EyeOff, text: `학습 제외 · ${EXCLUDE_REASON_NAMES[props.excludeReason]}` }
  return null
})
</script>

<template>
  <span v-if="state" class="inline-grid size-6 place-items-center text-muted-foreground" :title="state.text" :aria-label="state.text">
    <component :is="state.icon" class="size-3.5" />
  </span>
</template>
