<!--
  시스템 줄 목록: 아이콘 · 이름 · 상태 낱말 · (태그) 값 (+ 끝에 등급 아이콘). 홈의 시스템 칸과 상태 화면이 같이 쓴다.
  태그는 내장 DB의 '내장'이다.
  showDetail이면 준비 조건이 안 된 줄 아래에 서버가 준 원래 설명을 작은 글자로 보인다(상태 화면).
-->
<script setup lang="ts">
import { CircleCheck, OctagonAlert } from '@lucide/vue'

import SourceTag from '@/system/pages/SourceTag.vue'
import type { SystemRow, SystemTone } from '@/system/status'

defineProps<{
  rows: SystemRow[]
  showDetail?: boolean
}>()

// 상태 낱말의 글자색
const WORD_CLASS: Record<SystemTone, string> = {
  good: 'text-success-ink',
  bad: 'text-danger-ink',
  off: 'text-muted-foreground',
}
</script>

<template>
  <dl class="px-4 py-1.5">
    <div v-for="row in rows" :key="row.key" class="py-2.5">
      <div class="flex items-center gap-3 text-ui">
        <component :is="row.icon" class="size-4 shrink-0 text-muted-foreground" />
        <dt class="w-[76px] shrink-0 font-medium">{{ row.name }}</dt>
        <dd class="inline-flex w-[60px] shrink-0 items-center gap-1.5 font-semibold" :class="WORD_CLASS[row.tone]">
          <span
            v-if="row.tone === 'off'"
            class="size-1.5 shrink-0 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset"
          />
          {{ row.word }}
        </dd>
        <dd class="flex min-w-0 flex-1 items-center gap-2 text-muted-foreground">
          <SourceTag v-if="row.tag" :label="row.tag" embedded />
          <span class="truncate" :title="row.value || undefined">{{ row.value }}</span>
        </dd>
        <CircleCheck v-if="row.tone === 'good'" class="size-4 shrink-0 text-success" />
        <OctagonAlert v-else-if="row.tone === 'bad'" class="size-4 shrink-0 text-danger" />
      </div>
      <dd
        v-if="showDetail && row.tone === 'bad' && row.detail && row.detail !== `${row.word} · ${row.value}`"
        class="mt-1 pl-7 font-mono text-meta break-all text-danger-ink"
      >
        {{ row.detail }}
      </dd>
    </div>
  </dl>
</template>
