<!--
  고르기 칸. 브라우저 기본 select에 입력칸(Input)과 같은 모양을 입히고 오른쪽에 ▾를 둔다.
  v-model로 값을 주고받고, 고를 것은 <option>으로 넣는다. 예: <option :value="null">없음</option>
  id·aria-invalid·disabled 같은 속성은 안쪽 select에 붙는다.
-->
<script setup lang="ts">
import { ChevronDown } from '@lucide/vue'
import type { HTMLAttributes } from 'vue'

import { cn } from '@/system/ui/cn'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  class?: HTMLAttributes['class']
}>()

const model = defineModel<string | null>()
</script>

<template>
  <div :class="cn('relative w-full min-w-0', props.class)">
    <select
      v-model="model"
      v-bind="$attrs"
      data-slot="native-select"
      class="h-8 w-full min-w-0 appearance-none truncate rounded-md border border-input bg-card pr-8 pl-2.5 text-ui text-foreground shadow-(--shadow-xs) transition-[border-color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/22 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40"
    >
      <slot />
    </select>
    <ChevronDown
      class="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground"
      aria-hidden="true"
    />
  </div>
</template>
