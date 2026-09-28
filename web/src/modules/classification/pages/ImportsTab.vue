<!--
  가져온 기록 탭 (#/classification/<id>/imports): 이 데이터셋에 문장을 넣은 가져오기들. 최근 것부터.
    표     원본(파일 이름 또는 repo · config) · 종류(파일 · 허깅페이스) · 상태 · 추가 · 건너뜀 · 새 라벨 · 시각 · 소요
    펼침   줄을 누르면 아래에 건너뛴 줄(줄 · 이유)과 새 라벨, 실패했으면 오류가 열린다.
  시스템 API(GET /api/v1/datasets/{id}/imports)로 읽는다. 새 라벨은 분류 가져오기가 result에 남긴 이름들이다.
  데이터를 더하는 [+ 데이터 추가]는 상단 바에 있다(#/classification/<id>/add, 가져오기 네 단계로 이 데이터셋에 더한다).
-->
<script setup lang="ts">
import {
  ChevronRight,
  CircleCheck,
  Clock,
  Database,
  FileSpreadsheet,
  History,
  LoaderCircle,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { listImports } from '@/system/api'
import { dateTimeText, durationText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import type { ImportRead, ImportSource, ImportStatus } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import LabelChip from '@/modules/classification/LabelChip.vue'
import { NEW_LABELS_KEY, type DatasetRead } from '@/modules/classification/types'

defineProps<{
  dataset: DatasetRead
}>()

// 읽는 동안 보일 회색 줄 수
const SKELETON_ROWS = 3

// 서버가 남기는 건너뛴 줄의 최대 수 (앞쪽 100줄)
const SKIPPED_LINES_KEPT = 100

const SOURCE_WORDS: Record<ImportSource, { name: string; icon: LucideIcon }> = {
  file: { name: '파일', icon: FileSpreadsheet },
  huggingface: { name: '허깅페이스', icon: Database },
}

const STATUS_WORDS: Record<ImportStatus, { name: string; icon: LucideIcon; iconClass: string; textClass: string }> = {
  done: { name: '완료', icon: CircleCheck, iconClass: 'text-success', textClass: 'text-success-ink' },
  failed: { name: '실패', icon: OctagonAlert, iconClass: 'text-danger', textClass: 'text-danger-ink' },
  queued: { name: '대기', icon: Clock, iconClass: 'text-muted-foreground', textClass: 'text-muted-foreground' },
  running: {
    name: '진행 중',
    icon: LoaderCircle,
    iconClass: 'animate-spin text-muted-foreground',
    textClass: 'text-foreground',
  },
}

// 머리 칸 · 몸 칸
const TH_CLASS =
  'h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 border-b px-2.5 whitespace-nowrap'

// 표의 칸 수 (펼친 줄이 모두 차지한다)
const COLUMN_COUNT = 9

const route = useRoute()

const datasetId = computed(() => Number(route.params.datasetId))
const imports = ref<ImportRead[] | null>(null)
const loadError = ref<string | null>(null)
// 펼친 가져오기 번호들
const expanded = ref(new Set<number>())
let controller: AbortController | null = null

watch(datasetId, loadImports, { immediate: true })
onBeforeUnmount(() => controller?.abort())

async function loadImports(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  imports.value = null
  loadError.value = null
  try {
    imports.value = await listImports(datasetId.value, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = errorMessage(error)
  }
}

function toggle(importId: number): void {
  const next = new Set(expanded.value)
  if (next.has(importId)) next.delete(importId)
  else next.add(importId)
  expanded.value = next
}

// 분류 가져오기가 새로 만든 라벨 이름들
function newLabels(read: ImportRead): string[] {
  const names = read.result[NEW_LABELS_KEY]
  return Array.isArray(names) ? names.filter((name): name is string => typeof name === 'string') : []
}
</script>

<template>
  <div class="max-w-[1320px] px-6 py-5 xl:px-8">
    <section class="card overflow-clip" aria-label="가져온 기록">
      <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <Skeleton v-if="!imports && !loadError" class="h-5 w-24" />
        <b v-else class="text-title font-semibold">가져오기 {{ fmt(imports?.length ?? 0) }}</b>
        <span v-if="imports?.length" class="text-ui text-muted-foreground">
          추가 합계
          <b class="font-semibold text-foreground">{{ fmt(imports.reduce((sum, read) => sum + read.rows_added, 0)) }}</b>
        </span>
      </div>

      <div v-if="loadError" class="flex h-28 items-center justify-center gap-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="text-muted-foreground">{{ loadError }}</span>
        <Button variant="outline" size="sm" @click="loadImports">다시 불러오기</Button>
      </div>

      <div v-else-if="imports?.length === 0" class="py-14 text-center">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
          <History class="size-5 text-muted-foreground" />
        </span>
        <div class="mt-3 text-body font-semibold">가져오기 0</div>
      </div>

      <table v-else class="w-full table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col class="w-10" />
          <col />
          <col class="w-[92px]" />
          <col class="w-[84px]" />
          <col class="w-[92px]" />
          <col class="w-[76px]" />
          <col class="w-[72px]" />
          <col class="w-[132px]" />
          <col class="w-[76px]" />
        </colgroup>
        <thead>
          <tr>
            <th :class="TH_CLASS" class="pl-4"><span class="sr-only">펼치기</span></th>
            <th :class="TH_CLASS">원본</th>
            <th :class="TH_CLASS">종류</th>
            <th :class="TH_CLASS">상태</th>
            <th :class="TH_CLASS" class="text-right">추가</th>
            <th :class="TH_CLASS" class="text-right">건너뜀</th>
            <th :class="TH_CLASS" class="text-right">새 라벨</th>
            <th :class="TH_CLASS">시각</th>
            <th :class="TH_CLASS" class="pr-4 text-right">소요</th>
          </tr>
        </thead>

        <tbody v-if="!imports" aria-busy="true">
          <tr v-for="index in SKELETON_ROWS" :key="index">
            <td v-for="cell in COLUMN_COUNT" :key="cell" :class="TD_CLASS"><Skeleton v-if="cell > 1" class="h-3.5 w-3/4" /></td>
          </tr>
        </tbody>

        <tbody v-else>
          <template v-for="read in imports" :key="read.id">
            <tr
              class="cursor-pointer [&>td]:transition-colors hover:[&>td]:bg-muted/65"
              :class="expanded.has(read.id) ? '[&>td]:bg-muted/40' : ''"
              :data-import-id="read.id"
              @click="toggle(read.id)"
            >
              <td :class="TD_CLASS" class="pr-0 pl-4">
                <button
                  type="button"
                  class="grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                  :aria-expanded="expanded.has(read.id)"
                  :aria-label="`가져오기 #${read.id} 펼치기`"
                  @click.stop="toggle(read.id)"
                >
                  <ChevronRight class="size-4 transition-transform" :class="{ 'rotate-90': expanded.has(read.id) }" />
                </button>
              </td>
              <td :class="TD_CLASS">
                <span class="flex min-w-0 items-center gap-2">
                  <component :is="SOURCE_WORDS[read.source].icon" class="size-4 shrink-0 text-muted-foreground" />
                  <span class="truncate font-medium" :title="read.source_name">{{ read.source_name }}</span>
                  <span class="shrink-0 font-mono text-meta text-subtle-foreground">#{{ read.id }}</span>
                </span>
              </td>
              <td :class="TD_CLASS">{{ SOURCE_WORDS[read.source].name }}</td>
              <td :class="TD_CLASS">
                <span class="inline-flex items-center gap-1.5 font-semibold" :class="STATUS_WORDS[read.status].textClass">
                  <component
                    :is="STATUS_WORDS[read.status].icon"
                    class="size-3.5"
                    :class="STATUS_WORDS[read.status].iconClass"
                  />
                  {{ STATUS_WORDS[read.status].name }}
                </span>
              </td>
              <td :class="TD_CLASS" class="text-right font-semibold">{{ fmt(read.rows_added) }}</td>
              <td :class="TD_CLASS" class="text-right">
                <span :class="read.rows_skipped ? 'font-semibold text-warning-ink' : 'text-subtle-foreground'">
                  {{ fmt(read.rows_skipped) }}
                </span>
              </td>
              <td :class="TD_CLASS" class="text-right">
                <span :class="newLabels(read).length ? '' : 'text-subtle-foreground'">{{ newLabels(read).length }}</span>
              </td>
              <td :class="TD_CLASS" class="text-muted-foreground">{{ dateTimeText(read.created_at) }}</td>
              <td :class="TD_CLASS" class="pr-4 text-right text-muted-foreground">
                {{ durationText(read.created_at, read.finished_at) || '—' }}
              </td>
            </tr>

            <tr v-if="expanded.has(read.id)" :data-import-detail="read.id">
              <td :colspan="COLUMN_COUNT" class="border-b bg-[color-mix(in_oklab,var(--muted)_30%,var(--card))] px-4 py-4">
                <div class="grid gap-x-8 gap-y-4 pl-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <div class="min-w-0">
                    <div class="flex items-center gap-2">
                      <span class="caps">건너뛴 줄</span>
                      <span class="count-pill">{{ fmt(read.rows_skipped) }}</span>
                      <span v-if="read.rows_skipped > read.skipped_lines.length" class="text-meta text-muted-foreground">
                        앞 {{ SKIPPED_LINES_KEPT }}줄
                      </span>
                    </div>
                    <div
                      v-if="read.skipped_lines.length"
                      class="mt-2 max-h-[240px] overflow-auto rounded-md border bg-card"
                    >
                      <table class="w-full border-separate border-spacing-0 text-ui">
                        <thead>
                          <tr>
                            <th :class="TH_CLASS" class="sticky top-0 w-20 pl-3 text-right">줄</th>
                            <th :class="TH_CLASS" class="sticky top-0">이유</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="skipped in read.skipped_lines" :key="skipped.line" class="group">
                            <td class="h-8 border-b pl-3 text-right font-mono text-meta text-muted-foreground group-last:border-b-0">
                              {{ skipped.line }}
                            </td>
                            <td class="h-8 border-b px-2.5 group-last:border-b-0">{{ skipped.reason }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <div v-else class="mt-2 text-ui text-subtle-foreground">없음</div>
                  </div>

                  <div class="min-w-0 space-y-4">
                    <div>
                      <div class="flex items-center gap-2">
                        <span class="caps">새 라벨</span>
                        <span class="count-pill">{{ newLabels(read).length }}</span>
                      </div>
                      <div v-if="newLabels(read).length" class="mt-2 flex flex-wrap gap-1">
                        <LabelChip v-for="name in newLabels(read)" :key="name" :name="name" />
                      </div>
                      <div v-else class="mt-2 text-ui text-subtle-foreground">없음</div>
                    </div>
                    <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-ui">
                      <dt class="text-muted-foreground">원본 줄</dt>
                      <dd class="font-semibold">{{ read.rows_total === null ? '—' : fmt(read.rows_total) }}</dd>
                      <dt class="text-muted-foreground">끝난 시각</dt>
                      <dd class="font-semibold">{{ dateTimeText(read.finished_at) || '—' }}</dd>
                      <template v-if="read.error">
                        <dt class="text-muted-foreground">오류</dt>
                        <dd class="font-medium break-words text-danger-ink">{{ read.error }}</dd>
                      </template>
                    </dl>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </section>
  </div>
</template>
