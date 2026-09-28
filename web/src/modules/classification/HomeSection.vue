<!--
  홈의 분류 칸: 현황 숫자 네 개(분류 데이터셋 · 준비 안 됨 · 확인 필요 · 준비됨)와 그 아래 데이터셋 표.
  데이터셋이 없으면 가져오기 칸: 형식 칩과 버튼 세 개(가져오기 화면 #/classification/new로 간다), 그 아래 내장 예시 데이터 목록.
  데이터셋이 있으면 검색 칸과 같은 표(이름 · 상태 · 문장 · 라벨 · 제외 · 휴지통)다. 이름을 누르면 그 데이터셋 화면으로 간다.
  상태는 진단 화면의 종합 상태와 같은 계산이다(글자 검사 넷 + 뜻 분석이 있으면 뜻 검사 셋, 심각이 있으면 준비 안 됨).
  목록을 먼저 보이고, 데이터셋마다 진단을 몇 개씩 나눠 물어 채운다.
-->
<script setup lang="ts">
import {
  CircleCheck,
  Database,
  FileSpreadsheet,
  FileText,
  FileUp,
  LoaderCircle,
  OctagonAlert,
  Plus,
  Tags,
  TriangleAlert,
} from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { GRADES, overallStatus, type Grade, type OverallStatus } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'
import ExampleList from '@/system/sources/ExampleList.vue'
import { Badge } from '@/system/ui/badge'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getMap, getOverview, installExample, listExamples } from '@/modules/classification/api'
import { datasets, loadDatasets, loadFailed } from '@/modules/classification/datasets'
import { buildChecks, buildMeaningChecks } from '@/modules/classification/diagnosis/checks'
import type { DatasetSummaryRead } from '@/modules/classification/types'

// 가져올 수 있는 형식
const SOURCE_CHIPS = [
  { icon: FileText, label: 'CSV' },
  { icon: FileSpreadsheet, label: '엑셀(.xlsx)' },
  { icon: Database, label: '허깅페이스' },
]

// 한 번에 물을 진단 수. 데이터셋이 많아도 진단 계산(표를 훑는 집계)이 DB를 한꺼번에 붙잡지 않게 몇 개씩 나눈다.
const STATUS_CONCURRENCY = 3

// 데이터셋마다 종합 상태. 키가 없으면 재는 중, null이면 못 잼(오류)
const statuses = ref<Record<number, OverallStatus | null>>({})

// 등급별 데이터셋 수. undefined = 재는 중, null = 모름(—). 가져오는 중이거나 못 잰 데이터셋은 세지 않는다.
const gradeCounts = computed<Record<Grade, number> | null | undefined>(() => {
  const list = datasets.value
  if (!list) return loadFailed.value ? null : undefined
  const counted = list.filter((dataset) => !dataset.importing)
  if (counted.some((dataset) => !(dataset.id in statuses.value))) return undefined
  const counts: Record<Grade, number> = { bad: 0, warn: 0, good: 0 }
  for (const dataset of counted) {
    const status = statuses.value[dataset.id]
    if (status) counts[status.grade] += 1
  }
  return counts
})

// 현황 숫자. undefined = 불러오는 중, null = 모름(—)
const tiles = computed(() => {
  const total = datasets.value?.length ?? (loadFailed.value ? null : undefined)
  const counts = gradeCounts.value
  const countOf = (grade: Grade) => (counts ? counts[grade] : counts)
  return [
    { icon: Tags, label: '분류 데이터셋', value: total },
    { icon: OctagonAlert, label: '준비 안 됨', value: countOf('bad') },
    { icon: TriangleAlert, label: '확인 필요', value: countOf('warn') },
    { icon: CircleCheck, label: '준비됨', value: countOf('good') },
  ]
})

/** 진단 화면과 같은 검사로 종합 상태를 센다: 글자 검사 넷 + 뜻 분석이 있으면 뜻 검사 셋. */
async function datasetStatus(datasetId: number): Promise<OverallStatus> {
  const [overview, map] = await Promise.all([getOverview(datasetId), getMap(datasetId)])
  const meaning = map.checks ? buildMeaningChecks(map.checks, datasetId) : []
  return overallStatus([...buildChecks(overview), ...meaning])
}

// 몇 번째 재기인지. 목록을 다시 읽어 새로 재기 시작하면 앞선 재기는 남은 데이터셋을 묻지 않고 멈춘다(두 번 묻지 않게).
let generation = 0

/** 가져오는 중이 아닌 데이터셋의 상태를 몇 개씩 나눠 잰다. 다시 잴 때도 앞의 값은 새 값이 올 때까지 둔다(깜빡이지 않게). */
async function loadStatuses(list: DatasetSummaryRead[]): Promise<void> {
  const current = ++generation
  const queue = list.filter((dataset) => !dataset.importing).map((dataset) => dataset.id)
  const worker = async () => {
    for (let id = queue.shift(); id !== undefined && current === generation; id = queue.shift()) {
      try {
        const status = await datasetStatus(id)
        statuses.value = { ...statuses.value, [id]: status }
      } catch {
        statuses.value = { ...statuses.value, [id]: null }
      }
    }
  }
  await Promise.all(Array.from({ length: STATUS_CONCURRENCY }, worker))
}

onMounted(loadDatasets)
// 목록을 다시 읽으면(가져오기 · 도우미가 끝남) 상태도 다시 잰다
watch(datasets, (list) => list && void loadStatuses(list), { immediate: true })
</script>

<template>
  <section aria-label="현황" class="card grid grid-cols-2 overflow-hidden md:grid-cols-4 md:divide-x">
    <div v-for="tile in tiles" :key="tile.label" class="px-[18px] py-3.5">
      <span class="flex items-center gap-1.5 text-ui text-muted-foreground">
        <component :is="tile.icon" class="size-4 text-subtle-foreground" />{{ tile.label }}
      </span>
      <Skeleton v-if="tile.value === undefined" class="mt-2 mb-1 h-6 w-8" />
      <span
        v-else
        class="mt-1 block text-kpi font-semibold tracking-[-0.02em]"
        :class="tile.value ? 'text-foreground' : 'text-subtle-foreground'"
      >
        {{ tile.value ?? '—' }}
      </span>
    </div>
  </section>

  <section
    v-if="loadFailed && !datasets"
    class="card flex h-24 items-center justify-center gap-3 text-ui"
    aria-label="데이터셋 목록 오류"
  >
    <span class="font-medium">데이터셋 목록</span>
    <span class="font-semibold text-danger-ink">오류</span>
    <Button variant="outline" size="sm" @click="loadDatasets">다시 불러오기</Button>
  </section>

  <section
    v-else-if="datasets?.length === 0"
    class="card flex flex-col items-center px-6 py-14 text-center"
    aria-label="데이터셋 없음"
  >
    <span class="grid size-11 place-items-center rounded-xl bg-muted">
      <Database class="size-5 text-muted-foreground" />
    </span>
    <h2 class="mt-3 text-title font-semibold">데이터셋 없음</h2>
    <div class="mt-3 flex flex-wrap justify-center gap-1.5">
      <Badge v-for="chip in SOURCE_CHIPS" :key="chip.label">
        <component :is="chip.icon" class="text-muted-foreground" />{{ chip.label }}
      </Badge>
    </div>
    <div class="mt-6 flex flex-wrap justify-center gap-2">
      <Button size="lg" as-child>
        <RouterLink to="/classification/new"><Plus />새 데이터셋 가져오기</RouterLink>
      </Button>
      <Button variant="outline" size="lg" as-child>
        <RouterLink :to="{ path: '/classification/new', query: { source: 'file' } }"><FileUp />파일 올리기</RouterLink>
      </Button>
      <Button variant="outline" size="lg" as-child>
        <RouterLink :to="{ path: '/classification/new', query: { source: 'huggingface' } }">
          <Database />허깅페이스에서 가져오기
        </RouterLink>
      </Button>
    </div>
  </section>

  <ExampleList
    v-if="datasets?.length === 0"
    :load="listExamples"
    :install="installExample"
    :dataset-route="(id: number) => `/classification/${id}`"
    @installed="loadDatasets"
  />

  <section v-else-if="datasets" class="card" aria-label="분류 데이터셋">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <Tags class="size-4 text-muted-foreground" />
      <h2 class="text-body font-semibold">분류 데이터셋</h2>
      <span class="rounded-full bg-muted px-1.5 text-caps font-semibold text-muted-foreground">
        {{ datasets.length }}
      </span>
      <Button variant="outline" size="sm" class="ml-auto" as-child>
        <RouterLink to="/classification/new"><Plus />가져오기</RouterLink>
      </Button>
    </div>
    <table class="w-full table-fixed text-ui">
      <colgroup>
        <col />
        <col class="w-[120px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
      </colgroup>
      <thead>
        <tr class="text-meta text-muted-foreground">
          <th class="h-8 px-4 text-left font-semibold">이름</th>
          <th class="px-2 text-left font-semibold">상태</th>
          <th class="px-2 text-right font-semibold">문장</th>
          <th class="px-2 text-right font-semibold">라벨</th>
          <th class="px-2 text-right font-semibold">제외</th>
          <th class="px-4 text-right font-semibold">휴지통</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="dataset in datasets" :key="dataset.id" class="border-t hover:bg-muted/40">
          <td class="h-11 px-4">
            <RouterLink :to="`/classification/${dataset.id}`" class="block truncate font-medium">{{ dataset.name }}</RouterLink>
          </td>
          <td class="px-2" :data-status="dataset.id">
            <span v-if="dataset.importing" class="inline-flex items-center gap-1 text-muted-foreground">
              <LoaderCircle class="size-3.5 animate-spin" />가져오는 중
            </span>
            <Skeleton v-else-if="!(dataset.id in statuses)" class="h-4 w-16" />
            <span
              v-else-if="statuses[dataset.id]"
              class="inline-flex items-center gap-1 font-semibold"
              :class="GRADES[statuses[dataset.id]!.grade].textClass"
            >
              <component
                :is="GRADES[statuses[dataset.id]!.grade].icon"
                class="size-3.5"
                :class="GRADES[statuses[dataset.id]!.grade].iconClass"
              />{{ statuses[dataset.id]!.label }}
            </span>
            <span v-else class="text-subtle-foreground">—</span>
          </td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.record_count) }}</td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.label_count) }}</td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.excluded_count) }}</td>
          <td class="px-4 text-right tabular-nums">{{ fmt(dataset.trash_count) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
