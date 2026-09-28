<!--
  홈의 최근 가져오기 칸. 지금은 데이터셋이 없을 때의 '가져오기 기록 없음'만 있다.
  데이터셋이 생기면 숨긴다. 가져오기 기록 목록은 가져오기 기능을 만든 뒤 채운다.
-->
<script setup lang="ts">
import { History } from '@lucide/vue'

import { Skeleton } from '@/system/ui/skeleton'

import { datasets, loadFailed } from '@/modules/classification/datasets'
</script>

<template>
  <section v-if="!datasets?.length" class="card flex flex-col" aria-label="최근 가져오기">
    <div class="flex h-11 shrink-0 items-center border-b px-4">
      <h2 class="text-body font-semibold">최근 가져오기</h2>
    </div>
    <div class="grid flex-1 place-items-center py-12">
      <span v-if="datasets" class="flex flex-col items-center gap-2 text-ui text-subtle-foreground">
        <History class="size-5" />가져오기 기록 없음
      </span>
      <span v-else-if="loadFailed" class="text-ui text-subtle-foreground">—</span>
      <Skeleton v-else class="h-4 w-32" />
    </div>
  </section>
</template>
