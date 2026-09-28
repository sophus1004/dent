<!--
  질의 표: 질의 · 정답 문서 · 정답 · 오답 · 출처 · 상태 · 표시. 머리줄은 상단 바 밑에 붙어 따라온다.
  질의는 두 줄까지, 정답 문서는 첫 정답의 제목(없으면 앞부분) + '외 n'. 줄을 누르면 오른쪽 패널에 연다(open).
  처음 불러오는 동안은 회색 줄, 결과가 없으면 '결과 없음', 실패하면 오류 칸. selectable이면 맨 앞에 고르기 칸(휴지통).
-->
<script setup lang="ts">
import { SearchX, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'

import { fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import Marks from '@/modules/retrieval/data/Marks.vue'
import StatusIcon from '@/modules/retrieval/data/StatusIcon.vue'
import { SOURCE_NAMES } from '@/modules/retrieval/data/filters'
import type { QueryRead } from '@/modules/retrieval/types'

const props = defineProps<{
  rows: QueryRead[] | null
  loading: boolean
  error: string | null
  openId: number | null
  // 패널이 열려 표가 좁은지. 좁으면 출처 칸을 숨기고 표시를 아이콘만 둔다.
  narrow: boolean
  canClear: boolean
  selectable?: boolean
}>()

const emit = defineEmits<{
  open: [query: QueryRead]
  clear: []
  retry: []
}>()

const selected = defineModel<number[]>('selected', { default: () => [] })

const SKELETON_ROWS = 12
const SKELETON_WIDTHS = ['92%', '78%', '85%', '64%', '88%', '72%']

const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 px-2.5 py-1.5 align-middle'

const columnCount = computed(() => (props.narrow ? 6 : 7) + (props.selectable ? 1 : 0))
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

function isDimmed(row: QueryRead): boolean {
  return row.is_trashed || row.exclude_reason !== null
}

function positiveText(row: QueryRead): string {
  const first = row.positives[0]
  if (!first) return ''
  return first.title || first.snippet
}
</script>

<template>
  <table class="w-full table-fixed border-separate border-spacing-0 text-ui">
    <colgroup>
      <col v-if="selectable" class="w-10" />
      <col />
      <col :class="narrow ? 'w-[28%]' : 'w-[30%]'" />
      <col class="w-[52px]" />
      <col class="w-[52px]" />
      <col v-if="!narrow" class="w-[56px]" />
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
        <th :class="[TH_CLASS, selectable ? '' : 'pl-4']">질의</th>
        <th :class="TH_CLASS">정답 문서</th>
        <th :class="[TH_CLASS, 'text-right']">정답</th>
        <th :class="[TH_CLASS, 'text-right']">오답</th>
        <th v-if="!narrow" :class="TH_CLASS">출처</th>
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
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']">
          <Skeleton class="h-3.5" :style="{ width: SKELETON_WIDTHS[index % SKELETON_WIDTHS.length] }" />
        </td>
        <td :class="TD_CLASS"><Skeleton class="h-3.5 w-3/4" /></td>
        <td :class="TD_CLASS" />
        <td :class="TD_CLASS" />
        <td v-if="!narrow" :class="TD_CLASS"><Skeleton class="h-3.5 w-9" /></td>
        <td :class="TD_CLASS" />
        <td :class="[TD_CLASS, 'pr-4']" />
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
        :data-query-id="row.id"
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
          <div class="line-clamp-2" :class="isDimmed(row) ? 'text-muted-foreground' : 'text-foreground'">{{ row.text }}</div>
        </td>
        <td :class="TD_CLASS">
          <div v-if="row.positives.length" class="flex min-w-0 items-center gap-1.5">
            <span class="truncate text-muted-foreground" :title="positiveText(row)">{{ positiveText(row) }}</span>
            <span v-if="row.positive_count > 1" class="shrink-0 text-meta text-subtle-foreground">외 {{ row.positive_count - 1 }}</span>
          </div>
          <span v-else class="text-subtle-foreground">—</span>
        </td>
        <td :class="[TD_CLASS, 'text-right tabular-nums', row.positive_count ? '' : 'text-danger-ink font-semibold']">
          {{ fmt(row.positive_count) }}
        </td>
        <td :class="[TD_CLASS, 'text-right tabular-nums text-muted-foreground']">{{ fmt(row.negative_count) }}</td>
        <td v-if="!narrow" :class="[TD_CLASS, 'text-muted-foreground']">{{ SOURCE_NAMES[row.source] }}</td>
        <td :class="TD_CLASS"><StatusIcon :trashed="row.is_trashed" :exclude-reason="row.exclude_reason" /></td>
        <td :class="[TD_CLASS, 'pr-4']">
          <span class="flex flex-wrap items-center gap-1"><Marks :marks="row.marks" :compact="narrow" /></span>
        </td>
      </tr>
    </tbody>
  </table>
</template>
