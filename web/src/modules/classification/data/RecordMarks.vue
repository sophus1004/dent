<!--
  문장 한 건의 표시 태그: 같은 문장 n · 라벨 충돌. 해당하는 것이 없으면 아무것도 그리지 않는다.
    같은 문장 n   글자까지 같은 문장(휴지통 밖)이 이것 말고 n건 더 있다. 라벨이 달라도 센다.
    라벨 충돌     같은 문장에 다른 라벨이 달린 것이 있다.
-->
<script setup lang="ts">
import { Copy, Tags } from '@lucide/vue'

import { fmt } from '@/system/format'

import type { RecordRead } from '@/modules/classification/types'

defineProps<{
  record: RecordRead
}>()

// 태그 하나의 모양: 흰 바탕 + 1px 테두리, 12px 글자
const MARK_CLASS =
  'inline-flex h-5 items-center gap-[3px] rounded-[5px] bg-card px-1.5 text-meta font-[550] whitespace-nowrap text-foreground shadow-[inset_0_0_0_1px_var(--border)] [&>svg]:size-3 [&>svg]:shrink-0'
</script>

<template>
  <span v-if="record.duplicate_count > 0" :class="MARK_CLASS">
    <Copy class="text-muted-foreground" />같은 문장 <b class="font-semibold">{{ fmt(record.duplicate_count) }}</b>
  </span>
  <span v-if="record.has_conflict" :class="MARK_CLASS">
    <Tags class="text-warning" />라벨 충돌
  </span>
</template>
