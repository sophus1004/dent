<!-- 툴팁 공통 설정(나타나기까지 기다리는 시간 등) (shadcn-vue). App.vue가 한 번 감싼다. -->
<script setup lang="ts">
import type { TooltipProviderProps } from "reka-ui"
import { TooltipProvider } from "reka-ui"

const props = withDefaults(defineProps<TooltipProviderProps>(), {
  delayDuration: 0,
})
</script>

<template>
  <TooltipProvider v-bind="props">
    <slot />
  </TooltipProvider>
</template>
