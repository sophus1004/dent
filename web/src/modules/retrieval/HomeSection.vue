<!--
  홈의 검색 칸: 검색 데이터셋 목록(이름 · 입구 단계 · 질의 · 문서 · 정답 · 오답).
  데이터셋이 없으면 한 줄: '검색 데이터셋 없음' · [가져오기]와 그 아래 내장 예시 데이터 목록. 누르면 그 데이터셋 화면으로 간다.
-->
<script setup lang="ts">
import { Plus, TextSearch } from '@lucide/vue'
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { fmt } from '@/system/format'
import ExampleList from '@/system/sources/ExampleList.vue'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { installExample, listExamples } from '@/modules/retrieval/api'
import { datasets, loadDatasets, loadFailed } from '@/modules/retrieval/datasets'
import StageNo from '@/modules/retrieval/StageNo.vue'

onMounted(loadDatasets)
</script>

<template>
  <section class="card" aria-label="검색 데이터셋">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <TextSearch class="size-4 text-muted-foreground" />
      <h2 class="text-body font-semibold">검색 데이터셋</h2>
      <span v-if="datasets" class="rounded-full bg-muted px-1.5 text-caps font-semibold text-muted-foreground">
        {{ datasets.length }}
      </span>
      <Button variant="outline" size="sm" class="ml-auto" as-child>
        <RouterLink to="/retrieval/new"><Plus />가져오기</RouterLink>
      </Button>
    </div>
    <div v-if="loadFailed && !datasets" class="flex h-14 items-center justify-center gap-3 text-ui">
      <span class="font-semibold text-danger-ink">오류</span>
      <Button variant="outline" size="sm" @click="loadDatasets">다시 불러오기</Button>
    </div>
    <div v-else-if="!datasets" class="px-4 py-4"><Skeleton class="h-4 w-48" /></div>
    <div v-else-if="datasets.length === 0" class="flex h-14 items-center justify-center text-ui text-subtle-foreground">
      데이터셋 없음 · 문서만 · 쌍 · MRC · 세 쌍 · 점수
    </div>
    <table v-else class="w-full table-fixed text-ui">
      <colgroup>
        <col />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
      </colgroup>
      <thead>
        <tr class="text-meta text-muted-foreground">
          <th class="h-8 px-4 text-left font-semibold">이름</th>
          <th class="px-2 text-left font-semibold">입구</th>
          <th class="px-2 text-right font-semibold">질의</th>
          <th class="px-2 text-right font-semibold">문서</th>
          <th class="px-2 text-right font-semibold">정답</th>
          <th class="px-4 text-right font-semibold">오답</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="dataset in datasets" :key="dataset.id" class="border-t hover:bg-muted/40">
          <td class="h-11 px-4">
            <RouterLink :to="`/retrieval/${dataset.id}`" class="block truncate font-medium">{{ dataset.name }}</RouterLink>
          </td>
          <td class="px-2">
            <span v-if="dataset.entry_stage" class="inline-flex items-center gap-1 text-muted-foreground">
              <StageNo :no="dataset.entry_stage" size="xs" />단계
            </span>
            <span v-else class="text-subtle-foreground">—</span>
          </td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.query_count) }}</td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.document_count) }}</td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.positive_count) }}</td>
          <td class="px-4 text-right tabular-nums">{{ fmt(dataset.negative_count) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
  <ExampleList
    v-if="datasets?.length === 0"
    :load="listExamples"
    :install="installExample"
    :dataset-route="(id: number) => `/retrieval/${id}`"
    @installed="loadDatasets"
  />
</template>
