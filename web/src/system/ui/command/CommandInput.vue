<!-- 명령 검색칸 (shadcn-vue). 오른쪽 자리(slot)에 esc 글쇠 같은 것을 넣는다. -->
<script setup lang="ts">
import type { ListboxFilterProps } from "reka-ui"
import type { HTMLAttributes } from "vue"
import { Search } from "@lucide/vue"
import { reactiveOmit } from "@vueuse/core"
import { ListboxFilter, useForwardProps } from "reka-ui"
import { cn } from '@/system/ui/cn'
import { useCommand } from "."

defineOptions({
  inheritAttrs: false,
})

const props = defineProps<ListboxFilterProps & {
  class?: HTMLAttributes["class"]
}>()

const delegatedProps = reactiveOmit(props, "class")

const forwardedProps = useForwardProps(delegatedProps)

const { filterState } = useCommand()
</script>

<template>
  <div
    data-slot="command-input-wrapper"
    class="flex h-12 items-center gap-2.5 border-b px-4"
  >
    <Search class="size-4 shrink-0 text-muted-foreground" />
    <ListboxFilter
      v-bind="{ ...forwardedProps, ...$attrs }"
      v-model="filterState.search"
      data-slot="command-input"
      auto-focus
      :class="cn('placeholder:text-subtle-foreground flex h-10 w-full bg-transparent text-body outline-hidden disabled:cursor-not-allowed disabled:opacity-50', props.class)"
    />
    <slot />
  </div>
</template>
