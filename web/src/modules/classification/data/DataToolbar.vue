<!--
  데이터 탭의 거르기 줄: 문장 검색 · 문제 · 라벨 · 상태 · [조건 지우기].
  검색은 치는 동안 기다렸다가(0.3초) 바꾸고, Esc로 지운다. / 글쇠로 검색 칸에 들어간다(부모가 focusSearch를 부른다).
  값은 부모가 주소에서 읽어 filters로 넘기고, 바꿀 때는 change로 바꿀 칸만 알린다. 이 부품은 주소를 모른다.
-->
<script setup lang="ts">
import {
  Check,
  Copy,
  EqualApproximately,
  Ruler,
  ScanSearch,
  Search,
  Tags,
  X,
  type LucideIcon,
} from '@lucide/vue'
import { watchDebounced } from '@vueuse/core'
import { computed, ref, watch } from 'vue'

import { fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { DropdownMenuItem, DropdownMenuLabel } from '@/system/ui/dropdown-menu'

import {
  DEFAULT_STATUS,
  NO_FILTERS,
  PROBLEM_NAMES,
  PROBLEMS,
  STATUS_NAMES,
  STATUSES,
  type DataFilters,
} from '@/modules/classification/data/filters'
import FilterToken from '@/system/data/FilterToken.vue'
import type { DatasetRead, RecordProblem, RecordStatus } from '@/modules/classification/types'

const props = defineProps<{
  filters: DataFilters
  // 라벨 목록과 건수
  dataset: DatasetRead
}>()

const emit = defineEmits<{
  change: [patch: Partial<DataFilters>]
}>()

// 검색어를 치고 나서 목록을 바꾸기까지 기다리는 시간(밀리초). 칠 때마다 요청하지 않게 한다.
const SEARCH_DELAY_MS = 300

// 검색어의 최대 길이. 백엔드(router.py의 SEARCH_MAX_LENGTH)와 같다.
const SEARCH_MAX_LENGTH = 200

// 문제마다 아이콘
const PROBLEM_ICONS: Record<RecordProblem, LucideIcon> = {
  duplicate: Copy,
  conflict: Tags,
  short: Ruler,
  near_duplicate: EqualApproximately,
  suspect: ScanSearch,
}

const searchInput = ref<HTMLInputElement | null>(null)
const searchText = ref(props.filters.q)
// 마지막으로 부모에 알린 검색어. 부모가 같은 값을 돌려주면 치는 중인 글자를 덮어쓰지 않는다.
let sentQuery = props.filters.q

const labelName = computed(() => {
  const label = props.dataset.labels.find((item) => item.id === props.filters.labelId)
  if (label) return label.name
  return props.filters.labelId === null ? null : `#${props.filters.labelId}`
})

// 상태별 건수
const statusCounts = computed<Record<RecordStatus, number>>(() => {
  const dataset = props.dataset
  return {
    active: dataset.record_count,
    included: dataset.included_count,
    excluded: dataset.excluded_count,
    trash: dataset.trash_count,
  }
})

// 걸려 있는 조건 수. 둘 이상이면 [조건 지우기]를 보인다(하나면 토큰의 ×로 충분하다).
const activeCount = computed(() => {
  const filters = props.filters
  const flags = [
    filters.q,
    filters.labelId !== null,
    filters.status !== DEFAULT_STATUS,
    filters.problem,
  ]
  return flags.filter(Boolean).length
})

// 뒤로 가기나 [조건 지우기]로 주소의 검색어가 바뀌면 칸도 따라 바꾼다.
watch(
  () => props.filters.q,
  (query) => {
    if (query === sentQuery) return
    sentQuery = query
    searchText.value = query
  },
)

watchDebounced(searchText, applySearch, { debounce: SEARCH_DELAY_MS })

function applySearch(): void {
  const query = searchText.value.trim()
  if (query === props.filters.q) return
  sentQuery = query
  emit('change', { q: query })
}

// Esc: 글자가 있으면 지우고 바로 목록을 바꾼다. 비어 있으면 칸에서 나온다.
function onSearchEscape(event: KeyboardEvent): void {
  if (!searchText.value) {
    searchInput.value?.blur()
    return
  }
  event.stopPropagation()
  searchText.value = ''
  applySearch()
}

function clearSearch(): void {
  searchText.value = ''
  applySearch()
  searchInput.value?.focus()
}

/** 검색 칸에 들어간다. / 글쇠로 부른다. */
function focusSearch(): void {
  searchInput.value?.focus()
  searchInput.value?.select()
}

defineExpose({ focusSearch })
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <label
      class="flex h-8 w-[260px] items-center gap-2 rounded-md border border-input bg-card px-2.5 shadow-(--shadow-xs) transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/22 max-lg:w-[200px]"
    >
      <Search class="size-[15px] shrink-0 text-muted-foreground" />
      <input
        ref="searchInput"
        v-model="searchText"
        type="text"
        class="min-w-0 flex-1 bg-transparent text-ui outline-none placeholder:text-subtle-foreground"
        placeholder="문장 검색"
        aria-label="문장 검색"
        :maxlength="SEARCH_MAX_LENGTH"
        spellcheck="false"
        autocomplete="off"
        @keydown.esc="onSearchEscape"
      />
      <button
        v-if="searchText"
        type="button"
        class="-mr-1 grid size-5 place-items-center rounded text-muted-foreground hover:text-foreground"
        aria-label="검색어 지우기"
        @click="clearSearch"
      >
        <X class="size-3.5" />
      </button>
      <span v-else class="kbd">/</span>
    </label>

    <FilterToken
      name="문제"
      :value="filters.problem ? PROBLEM_NAMES[filters.problem] : null"
      @clear="emit('change', { problem: null })"
    >
      <DropdownMenuLabel>문제</DropdownMenuLabel>
      <DropdownMenuItem v-for="problem in PROBLEMS" :key="problem" @select="emit('change', { problem })">
        <span class="grid size-4 place-items-center"><Check v-if="filters.problem === problem" class="size-4" /></span>
        <component :is="PROBLEM_ICONS[problem]" class="text-muted-foreground" />{{ PROBLEM_NAMES[problem] }}
      </DropdownMenuItem>
    </FilterToken>

    <FilterToken name="라벨" :value="labelName" @clear="emit('change', { labelId: null })">
      <DropdownMenuLabel>라벨</DropdownMenuLabel>
      <DropdownMenuItem v-if="!dataset.labels.length" disabled>라벨 없음</DropdownMenuItem>
      <DropdownMenuItem
        v-for="label in dataset.labels"
        :key="label.id"
        @select="emit('change', { labelId: label.id })"
      >
        <span class="grid size-4 place-items-center"><Check v-if="filters.labelId === label.id" class="size-4" /></span>
        <span class="min-w-0 flex-1 truncate">{{ label.name }}</span>
        <span class="ml-3 text-meta text-muted-foreground">{{ fmt(label.record_count) }}</span>
      </DropdownMenuItem>
    </FilterToken>

    <FilterToken
      name="상태"
      :value="filters.status === DEFAULT_STATUS ? null : STATUS_NAMES[filters.status]"
      @clear="emit('change', { status: DEFAULT_STATUS })"
    >
      <DropdownMenuLabel>상태</DropdownMenuLabel>
      <DropdownMenuItem v-for="status in STATUSES" :key="status" @select="emit('change', { status })">
        <span class="grid size-4 place-items-center"><Check v-if="filters.status === status" class="size-4" /></span>
        {{ STATUS_NAMES[status] }}
        <span class="ml-auto pl-3 text-meta text-muted-foreground">{{ fmt(statusCounts[status]) }}</span>
      </DropdownMenuItem>
    </FilterToken>

    <Button v-if="activeCount > 1" variant="quiet" size="sm" @click="emit('change', { ...NO_FILTERS })">
      조건 지우기
    </Button>
  </div>
</template>
