<!--
  내장 예시 데이터 목록 (모든 모듈): 설치하자마자 해 볼 수 있게 모듈이 함께 둔 데이터셋. 새 데이터셋 화면 · 홈의 빈 칸에 붙는다.
    머리   예시 데이터 · 수
    줄     이름 · 설명 · 줄 수 · 심은 문제 칩(없으면 '문제 없음') · [넣기] 또는 이미 넣었으면 [열기]
  [넣기]는 모듈의 예시 넣기 API(보통 가져오기 길 → 작업)를 부르고 그 데이터셋 화면으로 간다. 목록 · 넣기 · 주소는 모듈이 넘긴다.
-->
<script setup lang="ts">
import { ArrowRight, BookOpen, Download, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter, type RouteLocationRaw } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import type { ExampleRead, ImportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

const props = defineProps<{
  // 모듈의 예시 목록 · 넣기 API
  load: (signal?: AbortSignal) => Promise<ExampleRead[]>
  install: (key: string) => Promise<ImportRead>
  // 데이터셋 화면 주소
  datasetRoute: (datasetId: number) => RouteLocationRaw
}>()

const emit = defineEmits<{
  // 넣기를 시작했다 (사이드바 · 홈 목록을 다시 읽게)
  installed: [imported: ImportRead]
}>()

const router = useRouter()
const items = ref<ExampleRead[] | null>(null)
const error = ref<string | null>(null)
const installing = ref<string | null>(null)

async function refresh(): Promise<void> {
  try {
    items.value = await props.load()
    error.value = null
  } catch (failure) {
    error.value = errorMessage(failure)
  }
}

async function install(item: ExampleRead): Promise<void> {
  if (installing.value) return
  installing.value = item.key
  error.value = null
  try {
    const imported = await props.install(item.key)
    emit('installed', imported)
    await router.push(props.datasetRoute(imported.dataset_id))
  } catch (failure) {
    error.value = errorMessage(failure)
    await refresh()
  } finally {
    installing.value = null
  }
}

onMounted(refresh)
</script>

<template>
  <section class="card overflow-hidden" aria-label="예시 데이터" data-example-list>
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <BookOpen class="size-4 text-primary" />
      <h2 class="text-body font-semibold">예시 데이터</h2>
      <span v-if="items" class="rounded-full bg-muted px-1.5 text-caps font-semibold text-muted-foreground">{{ items.length }}</span>
      <span class="ml-auto text-meta text-muted-foreground">내장 · 바로 넣기</span>
    </div>
    <div v-if="error" class="flex items-center gap-1.5 border-b px-4 py-2 text-ui" role="alert">
      <TriangleAlert class="size-3.5 text-danger" /><span class="text-danger-ink">{{ error }}</span>
    </div>
    <div v-if="!items && !error" class="space-y-2 p-4" aria-busy="true">
      <Skeleton v-for="index in 2" :key="index" class="h-12 w-full" />
    </div>
    <ul v-else-if="items" class="divide-y">
      <li
        v-for="item in items"
        :key="item.key"
        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-3"
        :data-example="item.key"
      >
        <span class="min-w-0">
          <span class="flex flex-wrap items-center gap-x-2 text-body font-semibold">
            {{ item.name }}
            <span class="text-meta font-normal text-muted-foreground tabular-nums">{{ fmt(item.rows) }}줄</span>
          </span>
          <small class="block truncate text-meta text-muted-foreground" :title="item.description">{{ item.description }}</small>
          <span class="mt-1.5 flex flex-wrap gap-1">
            <span
              v-for="planted in item.planted"
              :key="planted.name"
              class="inline-flex h-5 items-center rounded-[5px] bg-muted px-1.5 text-[11px] font-semibold text-muted-foreground tabular-nums"
            >{{ planted.name }} {{ fmt(planted.count) }}{{ planted.unit }}</span>
            <span
              v-if="!item.planted.length"
              class="inline-flex h-5 items-center rounded-[5px] bg-success/10 px-1.5 text-[11px] font-semibold text-success-ink"
            >문제 없음</span>
          </span>
        </span>
        <Button v-if="item.dataset_id" variant="outline" size="sm" as-child>
          <RouterLink :to="datasetRoute(item.dataset_id)" :data-action="`example-open-${item.key}`">열기<ArrowRight /></RouterLink>
        </Button>
        <Button
          v-else
          type="button"
          size="sm"
          :disabled="installing !== null"
          :data-action="`example-install-${item.key}`"
          @click="install(item)"
        >
          <LoaderCircle v-if="installing === item.key" class="animate-spin" /><Download v-else />넣기
        </Button>
      </li>
    </ul>
  </section>
</template>
