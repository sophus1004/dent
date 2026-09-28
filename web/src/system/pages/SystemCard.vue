<!--
  홈의 시스템 칸: /readyz를 줄로 보인다. DB · 스키마 · 저장 공간 · 작업 실행기.
  머리에는 정상 · 오류 · 미연결 개수가 있다. 모델(임베딩 · Jev · LLM)은 옆의 모델 칸이 보인다.
-->
<script setup lang="ts">
import { CircleCheck } from '@lucide/vue'
import { computed } from 'vue'

import SystemRows from '@/system/pages/SystemRows.vue'
import { checkedOnce, readiness } from '@/system/readiness'
import { systemRows } from '@/system/status'
import { Skeleton } from '@/system/ui/skeleton'

// 불러오는 동안 보일 회색 줄 수 (시스템 줄 수와 같다)
const SKELETON_ROWS = 4

const rows = computed(() => (readiness.value ? systemRows(readiness.value) : []))
const count = (tone: string): number => rows.value.filter((row) => row.tone === tone).length
</script>

<template>
  <section class="card" aria-label="시스템">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <h2 class="text-body font-semibold">시스템</h2>
      <div class="ml-auto flex items-center gap-2.5">
        <!-- 개수: 아주 좁은 화면에서는 숨긴다 -->
        <span v-if="rows.length" class="inline-flex items-center gap-3 text-meta font-semibold max-sm:hidden">
          <span class="inline-flex items-center gap-1 text-success-ink">
            <CircleCheck class="size-3.5 text-success" />정상 {{ count('good') }}
          </span>
          <span v-if="count('bad')" class="text-danger-ink">오류 {{ count('bad') }}</span>
          <span v-if="count('off')" class="inline-flex items-center gap-1.5 text-muted-foreground">
            <span class="size-1.5 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset" />
            미연결 {{ count('off') }}
          </span>
        </span>
      </div>
    </div>

    <div v-if="!checkedOnce" class="space-y-4 px-4 py-4">
      <Skeleton v-for="index in SKELETON_ROWS" :key="index" class="h-4 w-full" />
    </div>
    <div v-else-if="!readiness" class="flex h-12 items-center gap-3 px-4 text-ui">
      <span class="font-medium">API 서버</span>
      <span class="font-semibold text-danger-ink">응답 없음</span>
    </div>
    <SystemRows v-else :rows="rows" />
  </section>
</template>
