<!--
  상태 화면 (#/status): /readyz를 홈의 시스템 · 모델 칸 모양으로 보인다.
  위에는 준비 상태 한 낱말과 개수, 아래에는 시스템 줄(이름 · 상태 · 값, 안 되는 준비 조건은 서버의 설명도)과
  모델 줄(임베딩 · Jev · LLM).
-->
<script setup lang="ts">
import { Activity, RefreshCw } from '@lucide/vue'
import { computed, onMounted } from 'vue'

import TopBar from '@/system/layout/TopBar.vue'
import ModelRows from '@/system/pages/ModelRows.vue'
import SystemRows from '@/system/pages/SystemRows.vue'
import { checkedOnce, readiness, refreshReadiness } from '@/system/readiness'
import { modelRows, systemRows } from '@/system/status'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

// 불러오는 동안 보일 회색 줄 수 (시스템 줄 수와 같다)
const SKELETON_ROWS = 4

const rows = computed(() => (readiness.value ? systemRows(readiness.value) : []))
const models = computed(() => (readiness.value ? modelRows(readiness.value) : []))
const count = (tone: string): number =>
  [...rows.value, ...models.value].filter((row) => row.tone === tone).length

// 준비 상태 낱말과 글자색
const verdict = computed(() => {
  if (!checkedOnce.value) return { word: '확인 중', tone: 'text-muted-foreground' }
  if (!readiness.value) return { word: 'API 서버 응답 없음', tone: 'text-danger-ink' }
  if (!readiness.value.ready) return { word: '준비 안 됨', tone: 'text-danger-ink' }
  return { word: '준비됨', tone: 'text-success-ink' }
})

onMounted(refreshReadiness)
</script>

<template>
  <TopBar :crumbs="[{ label: '상태', icon: Activity }]">
    <Button variant="outline" size="sm" @click="refreshReadiness"><RefreshCw />다시 확인</Button>
  </TopBar>

  <div class="mx-auto w-full max-w-[880px] space-y-4 px-5 py-7 md:px-8">
    <section class="card flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4" aria-label="준비 상태">
      <div>
        <div class="caps">준비 상태</div>
        <div class="text-verdict font-semibold tracking-[-0.02em]" :class="verdict.tone">{{ verdict.word }}</div>
      </div>
      <dl v-if="rows.length" class="ml-auto grid grid-cols-3 divide-x rounded-lg border text-center">
        <div class="px-4 py-1.5">
          <dt class="text-meta text-muted-foreground">정상</dt>
          <dd class="text-title font-semibold">{{ count('good') }}</dd>
        </div>
        <div class="px-4 py-1.5">
          <dt class="text-meta text-muted-foreground">오류</dt>
          <dd class="text-title font-semibold" :class="{ 'text-danger-ink': count('bad') }">{{ count('bad') }}</dd>
        </div>
        <div class="px-4 py-1.5">
          <dt class="text-meta text-muted-foreground">미연결</dt>
          <dd class="text-title font-semibold">{{ count('off') }}</dd>
        </div>
      </dl>
    </section>

    <section v-if="!checkedOnce || readiness" class="card" aria-label="시스템">
      <div class="flex h-11 items-center border-b px-4">
        <h2 class="text-body font-semibold">시스템</h2>
      </div>
      <div v-if="!checkedOnce" class="space-y-4 px-4 py-4">
        <Skeleton v-for="index in SKELETON_ROWS" :key="index" class="h-4 w-full" />
      </div>
      <SystemRows v-else :rows="rows" show-detail />
    </section>

    <section v-if="readiness" class="card" aria-label="모델">
      <div class="flex h-11 items-center border-b px-4">
        <h2 class="text-body font-semibold">모델</h2>
      </div>
      <ModelRows :rows="models" />
    </section>
  </div>
</template>
