<!--
  앱의 틀: 왼쪽 사이드바, 오른쪽 지금 화면, 그리고 ⌘K 팔레트와 연결 설정 대화상자, 오른쪽 아래 알림.
  화면은 저마다 맨 위에 상단 바를 둔다.
  켤 때 /readyz 묻기와 작업 결과 살피기(끝난 작업 알림 · 확인 안 한 실패 수)를 시작하고,
  파일을 받는 칸 밖에 끌어 놓은 파일을 브라우저가 열지 않게 막는다.
-->
<script setup lang="ts">
import { setLucideProps } from '@lucide/vue'
import { onMounted, ref } from 'vue'
import { RouterView } from 'vue-router'

import ConnectionDialog from '@/system/connections/ConnectionDialog.vue'
import { useJobAnnouncer } from '@/system/jobAnnounce'
import { startJobFeed } from '@/system/jobFeed'
import AppSidebar from '@/system/layout/AppSidebar.vue'
import CommandPalette from '@/system/layout/CommandPalette.vue'
import Toaster from '@/system/layout/Toaster.vue'
import { startReadinessPolling } from '@/system/readiness'
import { preventFileDropOutsideZones } from '@/system/sources/fileDrop'
import { TooltipProvider } from '@/system/ui/tooltip'

// 툴팁이 뜨기까지 기다리는 시간(밀리초)
const TOOLTIP_DELAY_MS = 200

// 아이콘 선 굵기
setLucideProps({ strokeWidth: 1.75 })

const isPaletteOpen = ref(false)
const { announce } = useJobAnnouncer()

onMounted(() => {
  startReadinessPolling()
  startJobFeed(announce)
  preventFileDropOutsideZones()
})
</script>

<template>
  <TooltipProvider :delay-duration="TOOLTIP_DELAY_MS">
    <div class="flex min-h-screen">
      <AppSidebar @open-palette="isPaletteOpen = true" />
      <div class="flex min-w-0 flex-1 flex-col">
        <RouterView />
      </div>
    </div>
    <CommandPalette v-model:open="isPaletteOpen" />
    <ConnectionDialog />
    <Toaster />
  </TooltipProvider>
</template>
