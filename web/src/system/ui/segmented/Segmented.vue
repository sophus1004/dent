<!-- 몇 가지 값 가운데 하나를 고르는 붙은 버튼 줄. v-model로 고른 값을 주고받는다. 고른 칸은 강조(보라) 바탕. -->
<script setup lang="ts" generic="T extends string | number">
import type { SegmentedOption } from '@/system/ui/segmented'

defineProps<{
  options: SegmentedOption<T>[]
  // 읽는 사람을 위한 이름 (화면 낭독기)
  label: string
  disabled?: boolean
}>()

const model = defineModel<T>({ required: true })
</script>

<template>
  <span
    class="inline-flex h-7 items-center gap-0.5 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)"
    role="radiogroup"
    :aria-label="label"
  >
    <button
      v-for="option in options"
      :key="String(option.value)"
      type="button"
      role="radio"
      class="h-[22px] min-w-7 rounded-[5px] px-2 text-ui font-[550] tabular-nums transition-colors disabled:opacity-45"
      :class="model === option.value ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
      :aria-checked="model === option.value"
      :disabled="disabled"
      :data-value="option.value"
      @click="model = option.value"
    >
      {{ option.label }}
    </button>
  </span>
</template>
