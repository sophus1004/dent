<!--
  연결 설정 대화상자 (작게, 큰 설정 화면이 아니다): 외부 모델 서버 연결을 한 곳에서 본다.
    임베딩   주소 · 모델(필수) · [연결 확인] [저장] [연결 끊기]
    Jev      주소 · 모델(비우면 자동) · [연결 확인] [저장] [연결 끊기]
    LLM      공급자 · 주소 · API 키 · 모델(목록에서 고르기) · [연결 확인] [저장] [연결 끊기]. 키는 가려서 받고 저장된 키는 힌트만.
  열 때마다 GET /api/v1/connections로 저장된 값과 마지막 확인 결과를 채운다.
  저장 · 끊기를 하면 바로 적용되고(다시 실행하지 않는다), 상태(/readyz)를 다시 물어 홈 · 사이드바도 바로 바뀐다.
  App.vue에 하나만 두고 openConnectionSettings()로 연다.
-->
<script setup lang="ts">
import { Bot, Gavel, Plug, TriangleAlert, Waypoints } from '@lucide/vue'
import { ref, watch } from 'vue'

import { listConnections } from '@/system/api'
import ConnectionRow from '@/system/connections/ConnectionRow.vue'
import { isConnectionSettingsOpen } from '@/system/connections/connections'
import { errorMessage } from '@/system/http'
import { refreshReadiness } from '@/system/readiness'
import type { ConnectionRead, ConnectionRole } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'

// 태그 모양 (잠김 칩과 같다)
const TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]'

const connections = ref<ConnectionRead[] | null>(null)
const loadError = ref<string | null>(null)

watch(isConnectionSettingsOpen, (isOpen) => {
  if (isOpen) void loadConnections()
})

async function loadConnections(): Promise<void> {
  connections.value = null
  loadError.value = null
  try {
    connections.value = await listConnections()
  } catch (error) {
    loadError.value = errorMessage(error)
  }
}

function saved(role: ConnectionRole): ConnectionRead | null {
  return connections.value?.find((connection) => connection.role === role) ?? null
}
</script>

<template>
  <Dialog v-model:open="isConnectionSettingsOpen">
    <DialogContent
      class="w-[min(600px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="connection-settings"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
        <Plug class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">연결 설정</DialogTitle>
        <span :class="TAG_CLASS">저장 즉시 적용</span>
        <DialogDescription class="sr-only">외부 모델 서버: 임베딩 · Jev · LLM</DialogDescription>
      </DialogHeader>

      <div v-if="loadError" class="flex items-center gap-2 border-b px-5 py-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="min-w-0 flex-1 truncate text-muted-foreground">{{ loadError }}</span>
        <Button type="button" variant="outline" size="sm" @click="loadConnections">다시 불러오기</Button>
      </div>

      <div class="divide-y">
        <ConnectionRow
          role="embedding"
          :icon="Waypoints"
          name="임베딩"
          address-placeholder="http://127.0.0.1:8020/v1"
          model-placeholder="필수"
          model-required
          :initial="saved('embedding')"
          :loading="!connections && !loadError"
          @changed="refreshReadiness"
        />
        <ConnectionRow
          role="jev"
          :icon="Gavel"
          name="Jev"
          address-placeholder="http://127.0.0.1:8010"
          model-placeholder="자동"
          :model-required="false"
          :initial="saved('jev')"
          :loading="!connections && !loadError"
          @changed="refreshReadiness"
        />
        <ConnectionRow
          role="llm"
          :icon="Bot"
          name="LLM"
          address-placeholder="http://127.0.0.1:8000/v1"
          model-placeholder="비우고 [연결 확인] → 목록"
          :model-required="false"
          :initial="saved('llm')"
          :loading="!connections && !loadError"
          llm
          @changed="refreshReadiness"
        />
      </div>
    </DialogContent>
  </Dialog>
</template>
