<!--
  홈의 모델 칸: /readyz의 models를 줄로 보인다. 임베딩 · Jev · LLM.
  머리에는 정상 · 준비 중 · 오류 · 미연결 개수, 내장 모델 수 태그, [연결 설정] 버튼(연결 설정 대화상자를 연다).
  내장 모델을 받거나 불러오는 동안은 readiness가 2초마다 다시 물어 진행 막대가 움직인다.
-->
<script setup lang="ts">
import { CircleCheck, Settings2 } from '@lucide/vue'
import { computed } from 'vue'

import { openConnectionSettings } from '@/system/connections/connections'
import ModelRows from '@/system/pages/ModelRows.vue'
import SourceTag from '@/system/pages/SourceTag.vue'
import { checkedOnce, readiness } from '@/system/readiness'
import { modelRows, type ModelTone } from '@/system/status'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

// 불러오는 동안 보일 회색 줄 수 (모델 줄 수와 같다)
const SKELETON_ROWS = 3

const rows = computed(() => (readiness.value ? modelRows(readiness.value) : []))
const count = (tone: ModelTone): number => rows.value.filter((row) => row.tone === tone).length
const embeddedCount = computed(() => rows.value.filter((row) => row.embedded).length)
</script>

<template>
  <section class="card" aria-label="모델">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <h2 class="text-body font-semibold">모델</h2>
      <div class="ml-auto flex items-center gap-2.5">
        <!-- 개수: 아주 좁은 화면에서는 숨긴다 -->
        <span v-if="rows.length" class="inline-flex items-center gap-3 text-meta font-semibold max-sm:hidden">
          <span v-if="count('good')" class="inline-flex items-center gap-1 text-success-ink">
            <CircleCheck class="size-3.5 text-success" />정상 {{ count('good') }}
          </span>
          <span v-if="count('run')" class="text-accent-foreground">준비 중 {{ count('run') }}</span>
          <span v-if="count('bad')" class="text-danger-ink">오류 {{ count('bad') }}</span>
          <span v-if="count('off')" class="inline-flex items-center gap-1.5 text-muted-foreground">
            <span class="size-1.5 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset" />
            미연결 {{ count('off') }}
          </span>
          <SourceTag v-if="embeddedCount" :label="`내장 ${embeddedCount}`" embedded />
        </span>
        <Button type="button" variant="quiet" size="xs" data-action="connection-settings" @click="openConnectionSettings">
          <Settings2 />연결 설정
        </Button>
      </div>
    </div>

    <div v-if="!checkedOnce" class="space-y-6 px-4 py-5">
      <Skeleton v-for="index in SKELETON_ROWS" :key="index" class="h-5 w-full" />
    </div>
    <div v-else-if="!readiness" class="flex h-12 items-center gap-3 px-4 text-ui">
      <span class="font-medium">API 서버</span>
      <span class="font-semibold text-danger-ink">응답 없음</span>
    </div>
    <ModelRows v-else :rows="rows" />
  </section>
</template>
