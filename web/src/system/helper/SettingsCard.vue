<!--
  도우미 실행 설정 카드의 틀. 무리 · 칸은 모듈이 넣는다(SettingsGroup · SettingsField).
    머리   실행 설정 · 저장 중 · [기본값] · 닫기(다시 열었을 때)
    몸     모듈의 무리들 (흐름 전체 · 문서 나누기 · 질의 만들기 … / 분류: 새 문장)
    바닥   짧은 글(허락은 그 단계에서 따로 …) · [시작] (도우미 창 처음에만)
  값을 바꾸면 모듈이 바로 데이터셋 설정에 저장한다. 도우미 창이 처음(실행 없음)에 보이고, 끝난 뒤 머리의 [설정] ·
  허락 카드의 [설정]으로 다시 연다(허락 카드는 값을 읽기만 한다).
-->
<script setup lang="ts">
import { LoaderCircle, Play, RotateCcw, SlidersHorizontal, TriangleAlert, X } from '@lucide/vue'

import { Button } from '@/system/ui/button'

defineProps<{
  // 저장하는 중
  saving: boolean
  // 저장 · 읽기 실패 문장
  error: string | null
  // 바닥의 짧은 글
  note: string
  // [시작]을 보일지 (도우미 창 처음) · 누를 수 있는지 (LLM 연결 · 보내는 중이 아님)
  startable: boolean
  canStart: boolean
  // 닫기를 보일지 (끝난 뒤 · 허락 중에 다시 연 카드)
  closable: boolean
}>()

const emit = defineEmits<{
  reset: []
  start: []
  close: []
}>()
</script>

<template>
  <section class="overflow-hidden rounded-[11px] border bg-card shadow-(--shadow-card)" data-helper-settings>
    <div class="flex min-h-9 items-center gap-1.5 border-b px-3 text-ui">
      <SlidersHorizontal class="size-3.5 text-primary" />
      <b class="font-semibold">실행 설정</b>
      <LoaderCircle v-if="saving" class="size-3 animate-spin text-subtle-foreground" aria-label="저장 중" />
      <button
        type="button"
        class="ml-auto inline-flex items-center gap-1 text-meta font-semibold text-accent-foreground hover:underline"
        data-action="settings-reset"
        @click="emit('reset')"
      >
        <RotateCcw class="size-3" />기본값
      </button>
      <Button v-if="closable" type="button" variant="quiet" size="icon-sm" class="-mr-1.5" aria-label="닫기" @click="emit('close')">
        <X />
      </Button>
    </div>
    <slot />
    <div v-if="error" class="flex items-start gap-1.5 border-t px-3 py-2 text-ui" role="alert">
      <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger" />
      <span class="text-danger-ink">{{ error }}</span>
    </div>
    <div v-if="startable" class="flex min-h-11 items-center gap-2 border-t bg-muted/35 px-3 text-meta text-muted-foreground">
      {{ note }}
      <Button type="button" size="sm" class="ml-auto" :disabled="!canStart" data-action="helper-start" @click="emit('start')">
        <Play />시작
      </Button>
    </div>
  </section>
</template>
