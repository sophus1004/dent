<!--
  라벨 탭 (#/classification/<id>/labels): 데이터셋의 라벨 목록.
    표   라벨 · 건수 · 비율(막대) · 라벨 기준 · 고치기
  - 라벨 · 건수 · 기준은 머리(DatasetLayout)가 읽은 데이터셋(GET datasets/{id})의 labels. 이름 순, 건수는 학습 포함.
  - 줄을 누르면 데이터 탭을 그 라벨(학습 포함)로 거른다.
  [라벨 추가]와 줄마다 [고치기](이름 · 기준)는 아직 아무 일도 하지 않는다.
-->
<script setup lang="ts">
import { Pencil, Plus, Tags } from '@lucide/vue'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fmt } from '@/system/format'
import { Button } from '@/system/ui/button'

import { dataQuery, percentText } from '@/modules/classification/diagnosis/checks'
import LabelChip from '@/modules/classification/LabelChip.vue'
import type { DatasetRead, LabelRead } from '@/modules/classification/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

// 머리 칸 · 몸 칸
const TH_CLASS =
  'h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 border-b px-2.5 group-last:border-b-0'

const route = useRoute()
const router = useRouter()

const labels = computed(() => props.dataset.labels)
const largestCount = computed(() => Math.max(1, ...labels.value.map((label) => label.record_count)))

function share(label: LabelRead): number {
  return props.dataset.included_count ? label.record_count / props.dataset.included_count : 0
}

function openLabel(label: LabelRead): void {
  void router.push({
    name: 'classification-data',
    params: { datasetId: route.params.datasetId },
    query: dataQuery({ labelId: label.id }),
  })
}
</script>

<template>
  <div class="max-w-[1320px] space-y-3 px-6 py-5 xl:px-8">
    <section class="card overflow-clip" aria-label="라벨">
      <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <span class="flex items-baseline gap-2">
          <b class="text-title font-semibold">라벨 {{ fmt(labels.length) }}</b>
        </span>
        <span class="text-ui text-muted-foreground">
          학습 포함 <b class="font-semibold text-foreground">{{ fmt(dataset.included_count) }}</b>
        </span>
        <!-- 기능 미구현: 사용자와 함께 구현 -->
        <Button type="button" variant="outline" size="sm" class="ml-auto" data-todo="label-add">
          <Plus />라벨 추가
        </Button>
      </div>

      <div v-if="!labels.length" class="py-14 text-center">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
          <Tags class="size-5 text-muted-foreground" />
        </span>
        <div class="mt-3 text-body font-semibold">라벨 0</div>
      </div>

      <table v-else class="w-full table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col class="w-[20%]" />
          <col class="w-[84px]" />
          <col class="w-[22%]" />
          <col />
          <col class="w-[52px]" />
        </colgroup>
        <thead>
          <tr>
            <th :class="TH_CLASS" class="pl-4">라벨</th>
            <th :class="TH_CLASS" class="text-right">건수</th>
            <th :class="TH_CLASS">비율</th>
            <th :class="TH_CLASS" class="pl-5">라벨 기준</th>
            <th :class="TH_CLASS" class="pr-4"><span class="sr-only">고치기</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="label in labels"
            :key="label.id"
            class="group cursor-pointer [&>td]:transition-colors hover:[&>td]:bg-muted/65"
            tabindex="0"
            :data-label-id="label.id"
            @click="openLabel(label)"
            @keydown.enter="openLabel(label)"
          >
            <td :class="TD_CLASS" class="pl-4"><LabelChip :name="label.name" /></td>
            <td :class="TD_CLASS" class="text-right font-semibold">{{ fmt(label.record_count) }}</td>
            <td :class="TD_CLASS">
              <span class="flex items-center gap-2.5">
                <span class="relative h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-grid">
                  <span
                    class="absolute inset-y-0 left-0 rounded-full bg-chart-bar"
                    :style="{ width: `${(label.record_count / largestCount) * 100}%` }"
                  />
                </span>
                <span class="w-12 shrink-0 text-right text-muted-foreground">{{ percentText(share(label)) }}</span>
              </span>
            </td>
            <td :class="TD_CLASS" class="pl-5">
              <span v-if="label.description" class="line-clamp-2">{{ label.description }}</span>
              <span v-else class="text-subtle-foreground">—</span>
            </td>
            <td :class="TD_CLASS" class="pr-4 text-right">
              <!-- 기능 미구현: 사용자와 함께 구현 -->
              <Button
                type="button"
                variant="quiet"
                size="icon-sm"
                data-todo="label-edit"
                :aria-label="`${label.name} 이름 · 기준 고치기`"
                @click.stop
                @keydown.enter.stop
              >
                <Pencil />
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
