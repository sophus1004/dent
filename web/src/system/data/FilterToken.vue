<!--
  거르기 하나: 걸려 있으면 '이름: 값 ×' 토큰, 비어 있으면 점선 '+ 이름' 버튼.
  토큰 본문이나 + 버튼을 누르면 고르기 목록이 펼쳐진다. 목록의 줄은 부모가 slot으로 넣는다. ×는 이 거르기만 지운다.
-->
<script setup lang="ts">
import { Plus, X } from '@lucide/vue'

import { Button } from '@/system/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/system/ui/dropdown-menu'

defineProps<{
  // 거르기 이름. 예: '라벨'
  name: string
  // 지금 고른 값의 이름. 비어 있으면 null
  value: string | null
}>()

const emit = defineEmits<{
  clear: []
}>()
</script>

<template>
  <DropdownMenu>
    <span
      v-if="value !== null"
      class="inline-flex h-8 items-center rounded-lg border border-border-strong bg-card text-ui shadow-(--shadow-xs)"
    >
      <DropdownMenuTrigger
        class="inline-flex h-full max-w-[240px] items-center gap-1.5 rounded-l-lg pr-1 pl-2.5 font-[550] outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span class="font-medium text-muted-foreground">{{ name }}:</span>
        <span class="truncate">{{ value }}</span>
      </DropdownMenuTrigger>
      <button
        type="button"
        class="inline-grid h-full w-[26px] place-items-center rounded-r-lg text-muted-foreground hover:bg-muted hover:text-foreground"
        :aria-label="`${name} 조건 지우기`"
        @click="emit('clear')"
      >
        <X class="size-3.5" />
      </button>
    </span>
    <DropdownMenuTrigger v-else as-child>
      <Button
        variant="quiet"
        size="sm"
        class="border-dashed border-border-strong hover:bg-card data-[state=open]:bg-card data-[state=open]:text-foreground"
      >
        <Plus />{{ name }}
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent>
      <slot />
    </DropdownMenuContent>
  </DropdownMenu>
</template>
