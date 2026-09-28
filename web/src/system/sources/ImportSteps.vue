<!--
  가져오기 단계 표시: ① 파일 고르기 — ② 형식 확인 — ③ 필드 맞추기 — ④ 가져오기.
  지난 단계는 체크, 지금 단계는 보라 동그라미, 남은 단계는 회색이다. 단계 이름은 화면(모듈)이 준다.
-->
<script setup lang="ts">
import { Check } from '@lucide/vue'

defineProps<{
  steps: string[]
  // 지금 단계 (1부터)
  current: number
}>()
</script>

<template>
  <ol class="card flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3" aria-label="단계">
    <li v-for="(step, index) in steps" :key="step" class="flex items-center gap-2 text-ui">
      <span v-if="index > 0" class="mr-1 h-px w-6 bg-border-strong" aria-hidden="true" />
      <span
        class="grid size-6 shrink-0 place-items-center rounded-full text-meta font-semibold"
        :class="
          index + 1 === current
            ? 'bg-primary text-primary-foreground'
            : index + 1 < current
              ? 'bg-accent text-accent-foreground'
              : 'bg-muted text-subtle-foreground ring-1 ring-border ring-inset'
        "
      >
        <Check v-if="index + 1 < current" class="size-3.5" aria-label="끝남" />
        <template v-else>{{ index + 1 }}</template>
      </span>
      <span
        :class="
          index + 1 === current
            ? 'font-semibold text-foreground'
            : index + 1 < current
              ? 'text-foreground'
              : 'text-subtle-foreground'
        "
        :aria-current="index + 1 === current ? 'step' : undefined"
      >
        {{ step }}
      </span>
    </li>
  </ol>
</template>
