<!-- 없는 주소로 왔을 때: 그 주소와 [홈으로] 버튼. -->
<script setup lang="ts">
import { House, MapPinOff } from '@lucide/vue'
import { RouterLink, useRoute } from 'vue-router'

import TopBar from '@/system/layout/TopBar.vue'
import { Button } from '@/system/ui/button'

const route = useRoute()
</script>

<template>
  <TopBar :crumbs="[{ label: '없는 화면' }]" />

  <div class="page">
    <section class="card flex flex-col items-center px-6 py-12 text-center" aria-label="없는 화면">
      <span class="grid size-11 place-items-center rounded-xl bg-muted">
        <MapPinOff class="size-5 text-muted-foreground" />
      </span>
      <div class="mt-3 text-title font-semibold">없는 화면</div>
      <div class="mt-1 font-mono text-meta break-all text-muted-foreground">#{{ route.fullPath }}</div>
      <Button variant="outline" class="mt-5" as-child>
        <RouterLink to="/"><House />홈으로</RouterLink>
      </Button>
    </section>
  </div>
</template>
