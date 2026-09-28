<!--
  문장 표: 문장 · 라벨 · 상태 · 표시 · 번호. 머리줄은 상단 바 밑에 붙어 따라온다.
  문장은 두 줄까지 보이고, 줄을 누르면 오른쪽 패널에 연다(open). 연 줄은 보라 바탕.
  같은 문장끼리 붙어 나오는 문제(중복 · 라벨 충돌)로 거르면 묶음 사이에 진한 줄을, 묶음 안에는 점선을 긋는다.
  처음 불러오는 동안은 회색 막대 줄, 결과가 없으면 '결과 없음', 실패하면 오류 칸. 다시 불러오는 동안은 줄을 흐리게 둔다.
  selectable이면 맨 앞에 고르기 칸을 둔다(휴지통의 되살리기용). 고른 번호는 v-model:selected로 주고받는다.
  머리의 칸은 이 쪽을 모두 고르거나 모두 풀고, 일부만 골랐으면 가로줄로 보인다.
-->
<script setup lang="ts">
import { SearchX, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'

import { normalizeText } from '@/system/text'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import RecordMarks from '@/modules/classification/data/RecordMarks.vue'
import RecordStatusIcon from '@/modules/classification/data/RecordStatusIcon.vue'
import LabelChip from '@/modules/classification/LabelChip.vue'
import type { RecordRead } from '@/modules/classification/types'

const props = defineProps<{
  // 이 쪽의 문장. 아직 못 읽었으면 null
  rows: RecordRead[] | null
  loading: boolean
  // 불러오기에 실패했을 때의 문장
  error: string | null
  // 오른쪽 패널에 연 문장 번호
  openId: number | null
  // 같은 문장끼리 붙어 나오는지(묶음 줄을 긋는다)
  grouped: boolean
  // 패널이 열려 표가 좁은지. 좁으면 번호 칸을 숨기고(패널에 번호가 있다) 라벨 · 표시 칸을 줄인다.
  narrow: boolean
  // 거르기가 걸려 있어 [조건 지우기]를 보일지
  canClear: boolean
  // 맨 앞에 고르기 칸을 둘지
  selectable?: boolean
}>()

const emit = defineEmits<{
  open: [record: RecordRead]
  clear: []
  retry: []
}>()

// 고른 문장 번호 (selectable일 때)
const selected = defineModel<number[]>('selected', { default: () => [] })

// 불러오는 동안 보일 회색 줄 수와, 문장 막대의 폭(줄마다 조금씩 다르게)
const SKELETON_ROWS = 12
const SKELETON_WIDTHS = ['92%', '78%', '85%', '64%', '88%', '72%']

// 머리 칸: 흐린 회색 바탕, 상단 바(52px) 밑에 붙는다.
const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'

// 몸 칸
const TD_CLASS = 'h-10 px-2.5 py-1.5 align-middle'

const showId = computed(() => !props.narrow)
const columnCount = computed(() => (showId.value ? 5 : 4) + (props.selectable ? 1 : 0))

// 이 쪽의 문장 번호와, 그 가운데 고른 수
const pageIds = computed(() => (props.rows ?? []).map((record) => record.id))
const selectedOnPage = computed(() => pageIds.value.filter((id) => selected.value.includes(id)).length)
const isAllSelected = computed(() => pageIds.value.length > 0 && selectedOnPage.value === pageIds.value.length)
const isPartlySelected = computed(() => selectedOnPage.value > 0 && !isAllSelected.value)

/** 이 쪽을 모두 고른다. 이미 모두 골랐으면 모두 푼다. */
function toggleAll(): void {
  selected.value = isAllSelected.value ? [] : [...pageIds.value]
}

function toggle(recordId: number): void {
  const isSelected = selected.value.includes(recordId)
  selected.value = isSelected ? selected.value.filter((id) => id !== recordId) : [...selected.value, recordId]
}

// 같은 문장 묶음의 열쇠. 백엔드가 해시(정규화한 문장) 순으로 주므로 같은 열쇠끼리 붙어 있다.
const groupKeys = computed(() => (props.rows ?? []).map((record) => normalizeText(record.text)))

function rowClass(record: RecordRead, index: number): string[] {
  const classes: string[] = []
  const isOpen = record.id === props.openId
  classes.push(
    isOpen
      ? '[&>td]:bg-accent [&>td:first-child]:shadow-[inset_2px_0_0_var(--primary)]'
      : 'hover:[&>td]:bg-muted/65',
  )
  const isLast = index === groupKeys.value.length - 1
  if (isLast) return classes
  // 묶음 끝이면 진한 줄, 묶음 안이면 점선
  const endsGroup = groupKeys.value[index] !== groupKeys.value[index + 1]
  if (props.grouped && endsGroup) classes.push('[&>td]:border-b-border-strong')
  else if (props.grouped) classes.push('[&>td]:border-dashed')
  classes.push('[&>td]:border-b')
  return classes
}

// 학습에서 뺐거나 휴지통에 있는 문장은 흐리게
function isDimmed(record: RecordRead): boolean {
  return record.is_trashed || record.exclude_reason !== null
}
</script>

<template>
  <table class="w-full table-fixed border-separate border-spacing-0 text-ui">
    <colgroup>
      <col v-if="selectable" class="w-10" />
      <col />
      <col :class="narrow ? 'w-[128px]' : 'w-[152px] max-lg:w-[128px]'" />
      <col class="w-[52px]" />
      <!-- 표시: 넓으면 '같은 문장 n'과 '라벨 충돌'이 한 줄에 들어간다 -->
      <col :class="narrow ? 'w-[124px]' : 'w-[180px] max-lg:w-[132px]'" />
      <col v-if="showId" class="w-[80px]" />
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
            data-select="all"
            @change="toggleAll"
          />
        </th>
        <th :class="[TH_CLASS, selectable ? '' : 'pl-4']">문장</th>
        <th :class="TH_CLASS">라벨</th>
        <th :class="TH_CLASS">상태</th>
        <th :class="TH_CLASS">표시</th>
        <th v-if="showId" :class="[TH_CLASS, 'pr-4 text-right']"><span class="sr-only">번호</span></th>
      </tr>
    </thead>

    <tbody v-if="error">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
            <TriangleAlert class="size-5 text-danger" />
          </span>
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
        <td :class="TD_CLASS"><Skeleton class="h-5 w-16" /></td>
        <td :class="TD_CLASS" />
        <td :class="TD_CLASS" />
        <td v-if="showId" :class="[TD_CLASS, 'pr-4']"><Skeleton class="ml-auto h-3 w-10" /></td>
      </tr>
    </tbody>

    <tbody v-else-if="rows.length === 0">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
            <SearchX class="size-5 text-muted-foreground" />
          </span>
          <div class="mt-3 text-body font-semibold">결과 없음</div>
          <Button v-if="canClear" variant="outline" size="sm" class="mt-4" @click="emit('clear')">
            조건 지우기
          </Button>
        </td>
      </tr>
    </tbody>

    <tbody v-else class="transition-opacity" :class="{ 'opacity-60': loading }" :aria-busy="loading">
      <tr
        v-for="(record, index) in rows"
        :key="record.id"
        :data-record-id="record.id"
        class="cursor-pointer scroll-mt-[96px] scroll-mb-4 [&>td]:transition-colors"
        :class="rowClass(record, index)"
        :aria-current="record.id === openId ? 'true' : undefined"
        @click="emit('open', record)"
      >
        <td v-if="selectable" :class="[TD_CLASS, 'pr-0 pl-4']" @click.stop>
          <input
            type="checkbox"
            class="checkbox"
            :aria-label="`#${record.id} 고르기`"
            :checked="selected.includes(record.id)"
            :data-select="record.id"
            @change="toggle(record.id)"
          />
        </td>
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']">
          <div class="line-clamp-2" :class="isDimmed(record) ? 'text-muted-foreground' : 'text-foreground'">
            {{ record.text }}
          </div>
        </td>
        <td :class="TD_CLASS"><LabelChip :name="record.label_name" /></td>
        <td :class="TD_CLASS"><RecordStatusIcon :record="record" /></td>
        <td :class="TD_CLASS">
          <span class="flex flex-wrap items-center gap-1"><RecordMarks :record="record" /></span>
        </td>
        <td v-if="showId" :class="[TD_CLASS, 'pr-4 text-right font-mono text-meta text-subtle-foreground']">
          #{{ record.id }}
        </td>
      </tr>
    </tbody>
  </table>
</template>
