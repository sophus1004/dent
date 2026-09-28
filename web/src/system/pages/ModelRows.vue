<!--
  모델 줄 목록(임베딩 · Jev · LLM): 아이콘 · 이름 · (모델 이름 + 출처 태그 / 값 또는 내려받기 막대) · 상태 낱말 · 끝 아이콘.
  한 줄이 두 줄 높이라 옆의 시스템 칸(네 줄)과 높이가 맞는다. 미연결이면 [연결]이 연결 설정을 연다.
  홈의 모델 칸과 상태 화면이 같이 쓴다.
-->
<script setup lang="ts">
import { CircleCheck, Download, LoaderCircle, OctagonAlert, Plug } from '@lucide/vue'

import { openConnectionSettings } from '@/system/connections/connections'
import SourceTag from '@/system/pages/SourceTag.vue'
import type { ModelRow, ModelTone } from '@/system/status'
import { Button } from '@/system/ui/button'
import { Progress } from '@/system/ui/progress'

defineProps<{
  rows: ModelRow[]
}>()

// 상태 낱말의 글자색
const WORD_CLASS: Record<ModelTone, string> = {
  good: 'text-success-ink',
  run: 'text-accent-foreground',
  bad: 'text-danger-ink',
  off: 'text-muted-foreground',
}
</script>

<template>
  <dl class="py-1">
    <div
      v-for="row in rows"
      :key="row.key"
      class="grid min-h-[3.625rem] grid-cols-[1rem_3.5rem_minmax(0,1fr)_5.5rem_1rem] items-center gap-3 px-4 py-2 text-ui not-first:border-t not-first:border-border/60"
      :data-model="row.key"
    >
      <component :is="row.icon" class="size-4 text-muted-foreground" />
      <dt class="font-medium">{{ row.name }}</dt>
      <dd class="grid min-w-0 gap-1">
        <span v-if="row.tone === 'off' && !row.embedded">
          <Button type="button" variant="outline" size="xs" @click="openConnectionSettings"><Plug />연결</Button>
        </span>
        <template v-else>
          <span class="flex min-w-0 items-center gap-2">
            <b v-if="row.model" class="truncate font-semibold" :title="row.model">{{ row.model }}</b>
            <SourceTag v-if="row.source" :label="row.source" :embedded="row.embedded" />
          </span>
          <span v-if="row.progress" class="grid grid-cols-[minmax(3rem,1fr)_auto] items-center gap-2">
            <Progress :value="row.progress.fraction" :label="row.word" />
            <span class="text-meta whitespace-nowrap text-muted-foreground tabular-nums">{{ row.progress.text }}</span>
          </span>
          <span v-else-if="row.value" class="truncate text-meta text-muted-foreground" :title="row.value">
            {{ row.value }}
          </span>
        </template>
      </dd>
      <dd class="inline-flex items-center gap-1.5 font-semibold whitespace-nowrap" :class="WORD_CLASS[row.tone]">
        <span
          v-if="row.tone === 'off'"
          class="size-1.5 shrink-0 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset"
        />
        {{ row.word }}
      </dd>
      <CircleCheck v-if="row.tone === 'good'" class="size-4 text-success" />
      <OctagonAlert v-else-if="row.tone === 'bad'" class="size-4 text-danger" />
      <Download v-else-if="row.progress" class="size-4 text-primary" />
      <LoaderCircle v-else-if="row.tone === 'run'" class="size-4 animate-spin text-primary motion-reduce:animate-none" />
      <span v-else />
    </div>
  </dl>
</template>
