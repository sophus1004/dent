<!--
  화면 오른쪽 아래 알림. toasts.ts의 목록을 그린다.
    실패   빨간 줄 · 제목 '실패 · 종류' · 데이터셋 · 멈춘 곳 · 까닭 · [보기] [다시 하기] · 닫기
    완료   초록 줄 · 제목 '완료 · 종류' · 데이터셋 · 걸린 시간 (5초 뒤 저절로 닫힘)
-->
<script setup lang="ts">
import { CircleCheck, CircleX, X } from '@lucide/vue'

import { closeToast, toasts } from '@/system/toasts'
import { Button } from '@/system/ui/button'
</script>

<template>
  <div class="pointer-events-none fixed right-4 bottom-4 z-[60] grid w-[min(380px,calc(100vw-32px))] gap-2" aria-live="polite">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="pointer-events-auto grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2.5 rounded-[10px] border border-l-[3px] bg-popover px-3 py-2.5 shadow-(--shadow-pop)"
      :class="toast.tone === 'fail' ? 'border-l-danger' : 'border-l-success'"
      :role="toast.tone === 'fail' ? 'alert' : 'status'"
      :data-toast="toast.tone"
    >
      <CircleX v-if="toast.tone === 'fail'" class="mt-0.5 size-4 text-danger" />
      <CircleCheck v-else class="mt-0.5 size-4 text-success" />
      <div class="min-w-0">
        <div class="text-ui font-semibold">{{ toast.title }}</div>
        <div class="mt-0.5 line-clamp-2 text-meta break-words text-muted-foreground">{{ toast.detail }}</div>
        <div v-if="toast.actions.length" class="mt-2 flex gap-1.5">
          <Button
            v-for="action in toast.actions"
            :key="action.label"
            type="button"
            size="xs"
            :variant="action.primary ? 'default' : 'outline'"
            @click="closeToast(toast.id); action.run()"
          >
            {{ action.label }}
          </Button>
        </div>
      </div>
      <Button type="button" variant="quiet" size="icon-sm" class="-mt-0.5 -mr-1" aria-label="닫기" @click="closeToast(toast.id)">
        <X />
      </Button>
    </div>
  </div>
</template>
