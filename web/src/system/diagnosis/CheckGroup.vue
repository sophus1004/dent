<!--
  검사 묶음 카드(모든 모듈): 검사 카드들이 같은 모양으로 위아래에 선다(분류: 글자 · 뜻, 검색: 단계).
    머리   아이콘 이름 · 사실(facts slot, 예: 5종 · 규칙 계산 · 진단 19:25) · 오른쪽 끝 등급별 수(심각 2 · 주의 1 · 통과 2)
           · 버튼(actions slot, 예: 최신 · [의미 지도] · [다시 만들기])
    몸     CheckTable. checks가 비면 pending의 흐린 줄
  등급별 수는 수가 있는 등급만 보인다.
-->
<script setup lang="ts">
import type { LucideIcon } from '@lucide/vue'
import { computed } from 'vue'

import CheckTable from '@/system/diagnosis/CheckTable.vue'
import { GRADE_ORDER, GRADES, gradeCounts, type Check, type CheckWords } from '@/system/diagnosis/grades'

const props = defineProps<{
  icon: LucideIcon
  name: string
  checks: Check[]
  // 검사 이름 → 고정 낱말 (모듈이 준다)
  words: Record<string, CheckWords>
  pending?: string[]
}>()

defineSlots<{
  facts?(): unknown
  actions?(): unknown
}>()

const counts = computed(() => gradeCounts(props.checks))
const shownGrades = computed(() => GRADE_ORDER.filter((grade) => counts.value[grade] > 0))
</script>

<template>
  <section class="card overflow-hidden" :aria-label="name">
    <div class="flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1.5 border-b py-1.5 pr-3.5 pl-[18px]">
      <h2 class="inline-flex items-center gap-2 text-body font-semibold whitespace-nowrap">
        <component :is="icon" class="size-4 text-muted-foreground" />{{ name }}
      </h2>
      <span class="flex min-w-0 flex-wrap items-center gap-x-1.5 text-meta text-muted-foreground">
        <slot name="facts" />
      </span>
      <span class="ml-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1.5">
        <span v-if="shownGrades.length" class="inline-flex items-center gap-2.5 text-meta font-semibold" aria-label="등급별 수">
          <span
            v-for="grade in shownGrades"
            :key="grade"
            class="inline-flex items-center gap-1 whitespace-nowrap"
            :class="GRADES[grade].textClass"
          >
            <component :is="GRADES[grade].icon" class="size-3.5" :class="GRADES[grade].iconClass" :stroke-width="2.2" />
            {{ GRADES[grade].name }} {{ counts[grade] }}
          </span>
        </span>
        <slot name="actions" />
      </span>
    </div>
    <CheckTable :checks="checks" :words="words" :pending="pending" />
  </section>
</template>
