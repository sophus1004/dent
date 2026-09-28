<!-- 대화상자 안의 명령 목록 (shadcn-vue). ⌘K 팔레트가 쓴다. 화면 위쪽에 뜬다. -->
<script setup lang="ts">
import type { DialogRootEmits, DialogRootProps } from "reka-ui"
import { useForwardPropsEmits } from "reka-ui"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import Command from "./Command.vue"

const props = withDefaults(defineProps<DialogRootProps & {
  title?: string
  description?: string
}>(), {
  title: "검색 · 이동",
  description: "화면 · 명령",
})
const emits = defineEmits<DialogRootEmits>()

const forwarded = useForwardPropsEmits(props, emits)
</script>

<template>
  <Dialog v-slot="slotProps" v-bind="forwarded">
    <DialogContent
      :show-close-button="false"
      class="top-[14vh] w-[min(600px,calc(100vw-32px))] max-w-none translate-y-0 gap-0 overflow-hidden rounded-xl bg-popover p-0 shadow-(--shadow-pop) sm:max-w-none"
    >
      <DialogHeader class="sr-only">
        <DialogTitle>{{ title }}</DialogTitle>
        <DialogDescription>{{ description }}</DialogDescription>
      </DialogHeader>
      <Command>
        <slot v-bind="slotProps" />
      </Command>
    </DialogContent>
  </Dialog>
</template>
