<!--
  라벨 칩: 색 없이 글자 + 1px 테두리. 라벨마다 색을 따로 두지 않는다.
  이름이 길면 칸 폭에서 자른다. 라벨이 없으면(null) 흐린 —.
  count를 주면 이름 뒤에 굵은 건수를 붙인다(진단의 '대상' 칸: 최다 Business 31,900).
-->
<script setup lang="ts">
import { fmt } from '@/system/format'
import { Badge } from '@/system/ui/badge'

defineProps<{
  name: string | null
  count?: number | null
}>()
</script>

<template>
  <Badge v-if="name !== null" class="max-w-full" :title="name">
    <span class="min-w-0 truncate">{{ name }}</span>
    <b v-if="count !== undefined && count !== null" class="-ml-0.5 font-[650]">{{ fmt(count) }}</b>
  </Badge>
  <span v-else class="text-subtle-foreground">—</span>
</template>
