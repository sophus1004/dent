<!--
  문장의 상태 아이콘: 학습에서 뺐으면 눈 감은 아이콘, 휴지통에 있으면 휴지통 아이콘. 학습에 넣은 문장은 비워 둔다.
  아이콘에 올리면 이유(직접 뺌 · 중복 정리) 또는 휴지통에 넣은 시각이 뜬다.
-->
<script setup lang="ts">
import { EyeOff, Trash2 } from '@lucide/vue'

import { dateTimeText } from '@/system/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { EXCLUDE_REASON_NAMES } from '@/modules/classification/data/filters'
import type { RecordRead } from '@/modules/classification/types'

defineProps<{
  record: RecordRead
}>()
</script>

<template>
  <Tooltip v-if="record.is_trashed">
    <TooltipTrigger as-child>
      <span class="inline-grid size-6 place-items-center" aria-label="휴지통">
        <Trash2 class="size-4 text-muted-foreground" />
      </span>
    </TooltipTrigger>
    <TooltipContent>휴지통 · {{ dateTimeText(record.trashed_at) }}</TooltipContent>
  </Tooltip>
  <Tooltip v-else-if="record.exclude_reason">
    <TooltipTrigger as-child>
      <span class="inline-grid size-6 place-items-center" aria-label="학습 제외">
        <EyeOff class="size-4 text-muted-foreground" />
      </span>
    </TooltipTrigger>
    <TooltipContent>학습 제외 · {{ EXCLUDE_REASON_NAMES[record.exclude_reason] }}</TooltipContent>
  </Tooltip>
</template>
