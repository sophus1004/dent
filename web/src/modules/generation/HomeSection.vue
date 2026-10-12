<!--
  홈의 생성 칸: 생성 데이터셋 목록(이름 · 대화 · 문제 · 휴지통). 검색 칸과 같은 표다.
  데이터셋이 없으면 한 줄: '데이터셋 없음 · 받는 모양'과 [가져오기], 그 아래 내장 예시 데이터 목록. 이름을 누르면 그 데이터셋 화면으로 간다.
  문제는 문제가 남은 대화 수(같은 지시는 빼고 센다)라 진단의 검사별 수와 다를 수 있다.
-->
<script setup lang="ts">
import { MessageSquareText, Plus } from '@lucide/vue'
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { fmt } from '@/system/format'
import ExampleList from '@/system/sources/ExampleList.vue'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { installExample, listExamples } from '@/modules/generation/api'
import { datasets, loadDatasets, loadFailed } from '@/modules/generation/datasets'

onMounted(loadDatasets)
</script>

<template>
  <section class="card" aria-label="생성 데이터셋">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <MessageSquareText class="size-4 text-muted-foreground" />
      <h2 class="text-body font-semibold">생성 데이터셋</h2>
      <span v-if="datasets" class="rounded-full bg-muted px-1.5 text-caps font-semibold text-muted-foreground">
        {{ datasets.length }}
      </span>
      <Button variant="outline" size="sm" class="ml-auto" as-child>
        <RouterLink to="/generation/new"><Plus />가져오기</RouterLink>
      </Button>
    </div>
    <div v-if="loadFailed && !datasets" class="flex h-14 items-center justify-center gap-3 text-ui">
      <span class="font-semibold text-danger-ink">오류</span>
      <Button variant="outline" size="sm" @click="loadDatasets">다시 불러오기</Button>
    </div>
    <div v-else-if="!datasets" class="px-4 py-4"><Skeleton class="h-4 w-48" /></div>
    <div v-else-if="datasets.length === 0" class="flex h-14 items-center justify-center text-ui text-subtle-foreground">
      데이터셋 없음 · messages · ShareGPT · Alpaca · jsonl
    </div>
    <table v-else class="w-full table-fixed text-ui">
      <colgroup>
        <col />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
        <col class="w-[88px]" />
      </colgroup>
      <thead>
        <tr class="text-meta text-muted-foreground">
          <th class="h-8 px-4 text-left font-semibold">이름</th>
          <th class="px-2 text-right font-semibold">대화</th>
          <th class="px-2 text-right font-semibold">문제</th>
          <th class="px-4 text-right font-semibold">휴지통</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="dataset in datasets" :key="dataset.id" class="border-t hover:bg-muted/40">
          <td class="h-11 px-4">
            <RouterLink :to="`/generation/${dataset.id}`" class="block truncate font-medium">{{ dataset.name }}</RouterLink>
          </td>
          <td class="px-2 text-right tabular-nums">{{ fmt(dataset.conversation_count) }}</td>
          <td class="px-2 text-right tabular-nums" :class="dataset.problem_count ? 'font-semibold text-warning-ink' : 'text-subtle-foreground'">
            {{ fmt(dataset.problem_count) }}
          </td>
          <td class="px-4 text-right tabular-nums">{{ fmt(dataset.trash_count) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
  <ExampleList
    v-if="datasets?.length === 0"
    :load="listExamples"
    :install="installExample"
    :dataset-route="(id: number) => `/generation/${id}`"
    @installed="loadDatasets"
  />
</template>
