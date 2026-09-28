<!--
  데이터 탭의 거르기 줄: [질의 | 문서] · 검색 · 문제 · 출처(질의) · 쓰임(문서) · 상태 · [조건 지우기].
  검색은 치는 동안 기다렸다가(0.3초) 바꾸고, Esc로 지운다. / 글쇠로 검색 칸에 들어간다(부모가 focusSearch를 부른다).
  값은 부모가 주소에서 읽어 filters로 넘기고, 바꿀 때는 change로 바꿀 칸만 알린다.
-->
<script setup lang="ts">
import { Check, FileText, MessageCircleQuestion, Search, X } from '@lucide/vue'
import { watchDebounced } from '@vueuse/core'
import { computed, ref, watch } from 'vue'

import FilterToken from '@/system/data/FilterToken.vue'
import { fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { DropdownMenuItem, DropdownMenuLabel } from '@/system/ui/dropdown-menu'

import {
  DEFAULT_STATUS,
  DOCUMENT_PROBLEMS,
  MARKS,
  NO_FILTERS,
  QUERY_PROBLEMS,
  SOURCE_NAMES,
  SOURCES,
  STATUS_NAMES,
  STATUSES,
  USE_NAMES,
  USES,
  type DataFilters,
  type DataView,
} from '@/modules/retrieval/data/filters'
import type { DatasetRead } from '@/modules/retrieval/types'

const props = defineProps<{
  filters: DataFilters
  view: DataView
  dataset: DatasetRead
}>()

const emit = defineEmits<{
  change: [patch: Partial<DataFilters>]
  view: [view: DataView]
}>()

const SEARCH_DELAY_MS = 300
const SEARCH_MAX_LENGTH = 200

const KINDS: { key: 'queries' | 'documents'; name: string; icon: typeof FileText }[] = [
  { key: 'queries', name: '질의', icon: MessageCircleQuestion },
  { key: 'documents', name: '문서', icon: FileText },
]

const searchInput = ref<HTMLInputElement | null>(null)
const searchText = ref(props.filters.q)
let sentQuery = props.filters.q

const isDocuments = computed(() => props.view === 'documents')
const problems = computed(() => (isDocuments.value ? DOCUMENT_PROBLEMS : QUERY_PROBLEMS))

const activeCount = computed(() => {
  const filters = props.filters
  return [filters.q, filters.problem, filters.source, filters.use, filters.status !== DEFAULT_STATUS].filter(Boolean).length
})

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

function focusSearch(): void {
  searchInput.value?.focus()
  searchInput.value?.select()
}

defineExpose({ focusSearch })
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="질의 · 문서">
      <button
        v-for="kind in KINDS"
        :key="kind.key"
        type="button"
        class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
        :class="view === kind.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
        :aria-pressed="view === kind.key"
        :data-kind="kind.key"
        @click="emit('view', kind.key)"
      >
        <component :is="kind.icon" />{{ kind.name }}
        <span class="text-meta text-muted-foreground">{{ fmt(kind.key === 'queries' ? dataset.query_count : dataset.document_count) }}</span>
      </button>
    </div>

    <label
      class="flex h-8 w-[240px] items-center gap-2 rounded-md border border-input bg-card px-2.5 shadow-(--shadow-xs) transition-[border-color,box-shadow] focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/22 max-lg:w-[180px]"
    >
      <Search class="size-[15px] shrink-0 text-muted-foreground" />
      <input
        ref="searchInput"
        v-model="searchText"
        type="text"
        class="min-w-0 flex-1 bg-transparent text-ui outline-none placeholder:text-subtle-foreground"
        :placeholder="isDocuments ? '문서 검색' : '질의 검색'"
        :aria-label="isDocuments ? '문서 검색' : '질의 검색'"
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
      :value="filters.problem ? (MARKS[filters.problem]?.name ?? filters.problem) : null"
      @clear="emit('change', { problem: null })"
    >
      <DropdownMenuLabel>문제</DropdownMenuLabel>
      <DropdownMenuItem v-for="problem in problems" :key="problem" @select="emit('change', { problem })">
        <span class="grid size-4 place-items-center"><Check v-if="filters.problem === problem" class="size-4" /></span>
        <component :is="MARKS[problem].icon" class="text-muted-foreground" />{{ MARKS[problem].name }}
      </DropdownMenuItem>
    </FilterToken>

    <FilterToken
      v-if="!isDocuments"
      name="출처"
      :value="filters.source ? SOURCE_NAMES[filters.source] : null"
      @clear="emit('change', { source: null })"
    >
      <DropdownMenuLabel>출처</DropdownMenuLabel>
      <DropdownMenuItem v-for="source in SOURCES" :key="source" @select="emit('change', { source })">
        <span class="grid size-4 place-items-center"><Check v-if="filters.source === source" class="size-4" /></span>
        {{ SOURCE_NAMES[source] }}
      </DropdownMenuItem>
    </FilterToken>

    <FilterToken v-else name="쓰임" :value="filters.use ? USE_NAMES[filters.use] : null" @clear="emit('change', { use: null })">
      <DropdownMenuLabel>쓰임</DropdownMenuLabel>
      <DropdownMenuItem v-for="use in USES" :key="use" @select="emit('change', { use })">
        <span class="grid size-4 place-items-center"><Check v-if="filters.use === use" class="size-4" /></span>
        {{ USE_NAMES[use] }}
      </DropdownMenuItem>
    </FilterToken>

    <FilterToken
      name="상태"
      :value="filters.status === DEFAULT_STATUS ? null : STATUS_NAMES[filters.status]"
      @clear="emit('change', { status: DEFAULT_STATUS })"
    >
      <DropdownMenuLabel>상태</DropdownMenuLabel>
      <DropdownMenuItem
        v-for="status in STATUSES.filter((item) => !isDocuments || item === 'active' || item === 'trash')"
        :key="status"
        @select="emit('change', { status })"
      >
        <span class="grid size-4 place-items-center"><Check v-if="filters.status === status" class="size-4" /></span>
        {{ STATUS_NAMES[status] }}
      </DropdownMenuItem>
    </FilterToken>

    <Button v-if="activeCount > 1" variant="quiet" size="sm" @click="emit('change', { ...NO_FILTERS })">조건 지우기</Button>
  </div>
</template>
