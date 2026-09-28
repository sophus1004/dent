<!--
  데이터셋 지우기 확인 창. 데이터셋 머리의 [⋯] → [데이터셋 지우기]로 연다(분류와 같은 모양).
    머리   데이터셋 지우기 · '되돌릴 수 없음'
    몸     이름 · 질의 · 문서 · 판정 · 함께 지움 · 남김 / 이름 입력 칸 / 실패하면 까닭 한 줄
    바닥   [취소] · [지우기] (이름을 똑같이 쳐야 켜진다)
  DELETE /api/v1/retrieval/datasets/{id}. 가져오거나 뜻 분석을 만드는 중이면 서버가 409와 까닭을 준다.
-->
<script setup lang="ts">
import { LoaderCircle, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Input } from '@/system/ui/input'

import { deleteDataset } from '@/modules/retrieval/api'
import type { DatasetRead } from '@/modules/retrieval/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

const emit = defineEmits<{
  deleted: []
}>()

const open = defineModel<boolean>('open', { required: true })

const WARNING_TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-danger-ink shadow-[inset_0_0_0_1px_var(--danger)]'

const typedName = ref('')
const deleting = ref(false)
const error = ref<string | null>(null)

const isNameTyped = computed(() => typedName.value.trim() === props.dataset.name)

watch(open, (isOpen) => {
  if (!isOpen) return
  typedName.value = ''
  error.value = null
})

async function remove(): Promise<void> {
  if (!isNameTyped.value || deleting.value) return
  deleting.value = true
  error.value = null
  try {
    await deleteDataset(props.dataset.id)
    open.value = false
    emit('deleted')
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="w-[min(460px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="dataset-delete"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
        <Trash2 class="size-4 text-danger" />
        <DialogTitle class="text-title font-semibold">데이터셋 지우기</DialogTitle>
        <span :class="WARNING_TAG_CLASS">되돌릴 수 없음</span>
        <DialogDescription class="sr-only">데이터셋과 그 안의 질의 · 문서 · 판정 · 뜻 분석 · 가져온 기록을 지운다</DialogDescription>
      </DialogHeader>

      <form class="space-y-4 px-5 py-4" @submit.prevent="remove">
        <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
          <dt class="text-muted-foreground">이름</dt>
          <dd class="truncate font-semibold">{{ dataset.name }}</dd>
          <dt class="text-muted-foreground">질의</dt>
          <dd><b class="font-semibold">{{ fmt(dataset.query_count) }}</b></dd>
          <dt class="text-muted-foreground">문서</dt>
          <dd><b class="font-semibold">{{ fmt(dataset.document_count) }}</b></dd>
          <dt class="text-muted-foreground">판정</dt>
          <dd>
            <b class="font-semibold">{{ fmt(dataset.judgment_count) }}</b>
            <span v-if="dataset.trash_count" class="text-muted-foreground"> · 휴지통 {{ fmt(dataset.trash_count) }}</span>
          </dd>
          <dt class="text-muted-foreground">함께 지움</dt>
          <dd>뜻 분석 · 가져온 기록 · 내보낸 파일</dd>
          <dt class="text-muted-foreground">남김</dt>
          <dd>임베딩 캐시</dd>
        </dl>

        <label class="block">
          <span class="mb-1.5 block text-ui font-[550]">이름 입력</span>
          <Input
            v-model="typedName"
            :placeholder="dataset.name"
            autocomplete="off"
            spellcheck="false"
            aria-label="지울 데이터셋 이름"
            data-field="confirm-name"
          />
        </label>

        <div v-if="error" class="flex items-start gap-2 text-ui" role="alert">
          <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ error }}</span>
        </div>

        <div class="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" @click="open = false">취소</Button>
          <Button
            type="submit"
            class="bg-danger text-white hover:bg-danger/90"
            :disabled="!isNameTyped || deleting"
            data-action="delete-dataset"
          >
            <LoaderCircle v-if="deleting" class="animate-spin" />
            <Trash2 v-else />지우기
          </Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>
</template>
