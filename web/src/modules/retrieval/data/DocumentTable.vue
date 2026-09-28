<!--
  문서 표: 문서(제목 · 앞부분) · 청크 · 토큰 · 정답으로 · 오답으로 · 상태 · 표시.
  토큰이 최대(설정)를 넘으면 호박색. 줄을 누르면 오른쪽 패널에 연다(open). selectable이면 맨 앞에 고르기 칸.
-->
<script setup lang="ts">
import { SearchX, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'

import { fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import Marks from '@/modules/retrieval/data/Marks.vue'
import StatusIcon from '@/modules/retrieval/data/StatusIcon.vue'
import type { DocumentRead } from '@/modules/retrieval/types'

const props = defineProps<{
  rows: DocumentRead[] | null
  loading: boolean
  error: string | null
  openId: number | null
  narrow: boolean
  canClear: boolean
  // 문서 최대 토큰 (설정)
  maxTokens: number
  selectable?: boolean
}>()

const emit = defineEmits<{
  open: [document: DocumentRead]
  clear: []
  retry: []
}>()

const selected = defineModel<number[]>('selected', { default: () => [] })

const SKELETON_ROWS = 12

const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 px-2.5 py-1.5 align-middle'

const columnCount = computed(() => 7 + (props.selectable ? 1 : 0))
const pageIds = computed(() => (props.rows ?? []).map((row) => row.id))
const selectedOnPage = computed(() => pageIds.value.filter((id) => selected.value.includes(id)).length)
const isAllSelected = computed(() => pageIds.value.length > 0 && selectedOnPage.value === pageIds.value.length)
const isPartlySelected = computed(() => selectedOnPage.value > 0 && !isAllSelected.value)

function toggleAll(): void {
  selected.value = isAllSelected.value ? [] : [...pageIds.value]
}

function toggle(id: number): void {
  selected.value = selected.value.includes(id) ? selected.value.filter((item) => item !== id) : [...selected.value, id]
}
</script>

<template>
  <table class="w-full table-fixed border-separate border-spacing-0 text-ui">
    <colgroup>
      <col v-if="selectable" class="w-10" />
      <col />
      <col class="w-[56px]" />
      <col class="w-[64px]" />
      <col class="w-[64px]" />
      <col class="w-[64px]" />
      <col class="w-[40px]" />
      <col :class="narrow ? 'w-[76px]' : 'w-[168px] max-lg:w-[120px]'" />
    </colgroup>
    <thead>
      <tr>
        <th v-if="selectable" :class="[TH_CLASS, 'pr-0 pl-4']">
          <input
            type="checkbox"
            class="checkbox"
            aria-label="모두 고르기"
            :checked="isAllSelected"
            :indeterminate="isPartlySelected"
            :disabled="!pageIds.length"
            @change="toggleAll"
          />
        </th>
        <th :class="[TH_CLASS, selectable ? '' : 'pl-4']">문서</th>
        <th :class="TH_CLASS">청크</th>
        <th :class="[TH_CLASS, 'text-right']">토큰</th>
        <th :class="[TH_CLASS, 'text-right']">정답으로</th>
        <th :class="[TH_CLASS, 'text-right']">오답으로</th>
        <th :class="TH_CLASS"><span class="sr-only">상태</span></th>
        <th :class="[TH_CLASS, 'pr-4']">표시</th>
      </tr>
    </thead>

    <tbody v-if="error">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><TriangleAlert class="size-5 text-danger" /></span>
          <div class="mt-3 text-body font-semibold">불러오기 실패</div>
          <div class="mt-1 text-ui text-muted-foreground">{{ error }}</div>
          <Button variant="outline" size="sm" class="mt-4" @click="emit('retry')">다시 불러오기</Button>
        </td>
      </tr>
    </tbody>

    <tbody v-else-if="!rows" aria-busy="true">
      <tr v-for="index in SKELETON_ROWS" :key="index" class="[&>td]:border-b">
        <td v-if="selectable" :class="[TD_CLASS, 'pr-0 pl-4']" />
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']"><Skeleton class="h-3.5 w-4/5" /></td>
        <td v-for="cell in columnCount - 1 - (selectable ? 1 : 0)" :key="cell" :class="TD_CLASS" />
      </tr>
    </tbody>

    <tbody v-else-if="rows.length === 0">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><SearchX class="size-5 text-muted-foreground" /></span>
          <div class="mt-3 text-body font-semibold">결과 없음</div>
          <Button v-if="canClear" variant="outline" size="sm" class="mt-4" @click="emit('clear')">조건 지우기</Button>
        </td>
      </tr>
    </tbody>

    <tbody v-else class="transition-opacity" :class="{ 'opacity-60': loading }" :aria-busy="loading">
      <tr
        v-for="row in rows"
        :key="row.id"
        :data-document-id="row.id"
        class="cursor-pointer scroll-mt-[96px] scroll-mb-4 [&>td]:border-b [&>td]:transition-colors"
        :class="row.id === openId ? '[&>td]:bg-accent [&>td:first-child]:shadow-[inset_2px_0_0_var(--primary)]' : 'hover:[&>td]:bg-muted/65'"
        :aria-current="row.id === openId ? 'true' : undefined"
        @click="emit('open', row)"
      >
        <td v-if="selectable" :class="[TD_CLASS, 'pr-0 pl-4']" @click.stop>
          <input
            type="checkbox"
            class="checkbox"
            :aria-label="`#${row.id} 고르기`"
            :checked="selected.includes(row.id)"
            @change="toggle(row.id)"
          />
        </td>
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']">
          <div class="line-clamp-2" :class="row.is_trashed ? 'text-muted-foreground' : 'text-foreground'">
            <b v-if="row.title" class="font-semibold">{{ row.title }} · </b>{{ row.text.slice(0, 200) }}
          </div>
        </td>
        <td :class="[TD_CLASS, 'text-muted-foreground tabular-nums']">
          {{ row.chunk_index !== null ? `${row.chunk_index + 1}째` : '—' }}
        </td>
        <td :class="[TD_CLASS, 'text-right tabular-nums', row.token_count > maxTokens ? 'font-semibold text-warning-ink' : 'text-muted-foreground']">
          {{ fmt(row.token_count) }}
        </td>
        <td :class="[TD_CLASS, 'text-right tabular-nums']">{{ fmt(row.positive_count) }}</td>
        <td :class="[TD_CLASS, 'text-right tabular-nums text-muted-foreground']">{{ fmt(row.negative_count) }}</td>
        <td :class="TD_CLASS"><StatusIcon :trashed="row.is_trashed" /></td>
        <td :class="[TD_CLASS, 'pr-4']">
          <span class="flex flex-wrap items-center gap-1"><Marks :marks="row.marks" :compact="narrow" /></span>
        </td>
      </tr>
    </tbody>
  </table>
</template>
