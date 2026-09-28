<!-- 거른 결과가 없을 때 보이는 칸 (shadcn-vue). -->
<script setup lang="ts">
import type { PrimitiveProps } from "reka-ui"
import type { HTMLAttributes } from "vue"
import { reactiveOmit } from "@vueuse/core"
import { Primitive } from "reka-ui"
import { computed } from "vue"
import { cn } from '@/system/ui/cn'
import { useCommand } from "."

const props = defineProps<PrimitiveProps & { class?: HTMLAttributes["class"] }>()

const delegatedProps = reactiveOmit(props, "class")

const { filterState } = useCommand()
const isRender = computed(() => !!filterState.search && filterState.filtered.count === 0,
)
</script>

<template>
  <Primitive
    v-if="isRender"
    data-slot="command-empty"
    v-bind="delegatedProps" :class="cn('py-10 text-center text-ui text-muted-foreground', props.class)"
  >
    <slot />
  </Primitive>
</template>
