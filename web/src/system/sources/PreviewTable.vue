<!--
  원본 미리 보기 표: 왼쪽에 줄 번호, 그 옆에 원본의 열 그대로. 가져오기 2단계(형식 확인)가 쓴다.
  표는 자기 칸 안에서만 가로·세로로 스크롤하고, 머리줄과 줄 번호 칸은 스크롤해도 붙어 있다.
  긴 값은 한 줄로 자르고, 마우스를 올리면 전체를 보인다. 빈 값은 흐린 —.
  marks에 열 이름 → 짧은 표시를 주면 머리줄의 열 이름 옆에 붙인다. 예: { label: '라벨 7' }
-->
<script setup lang="ts">
import { showValue } from '@/system/format'
import type { PreviewRow } from '@/system/types'

withDefaults(
  defineProps<{
    columns: string[]
    rows: PreviewRow[]
    // rows 각 줄의 줄 번호
    lines: number[]
    marks?: Record<string, string>
  }>(),
  { marks: () => ({}) },
)
</script>

<template>
  <div class="max-h-[440px] overflow-auto">
    <table class="w-max min-w-full border-separate border-spacing-0 text-ui">
      <thead>
        <tr>
          <th
            scope="col"
            class="sticky top-0 left-0 z-[2] h-9 w-14 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-right text-meta font-semibold whitespace-nowrap text-muted-foreground"
          >
            줄
          </th>
          <th
            v-for="column in columns"
            :key="column"
            scope="col"
            class="sticky top-0 z-[1] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground"
          >
            <span class="inline-flex items-center gap-1.5">
              <span class="font-mono text-foreground">{{ column }}</span>
              <span
                v-if="marks[column]"
                class="rounded-sm px-1.5 text-caps font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]"
              >
                {{ marks[column] }}
              </span>
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="lines[index] ?? index" class="group">
          <td
            class="sticky left-0 h-10 border-b bg-card px-2.5 text-right font-mono text-meta text-subtle-foreground group-last:border-b-0"
          >
            {{ lines[index] }}
          </td>
          <td v-for="column in columns" :key="column" class="h-10 border-b px-2.5 group-last:border-b-0">
            <div
              v-if="showValue(row[column]).trim() !== ''"
              class="max-w-[320px] truncate"
              :title="showValue(row[column])"
            >
              {{ showValue(row[column]) }}
            </div>
            <span v-else class="text-subtle-foreground">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
