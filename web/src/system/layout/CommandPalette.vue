<!--
  ⌘K 팔레트: 화면 이동과 테마 바꾸기만 한다. ⌘K(Windows는 Ctrl+K)로 열고 닫는다.
  '작업' 묶음에는 각 모듈이 내놓은 바로가기(commands)가 먼저 온다. 예: 새 데이터셋 가져오기
-->
<script setup lang="ts">
import { Activity, History, House, SunMoon, type LucideIcon } from '@lucide/vue'
import { useEventListener } from '@vueuse/core'
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { useModules } from '@/system/module'
import { cycleTheme, nextTheme, theme, THEME_NAMES } from '@/system/theme'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/system/ui/command'

interface PaletteCommand {
  label: string
  icon: LucideIcon
  // 오른쪽 작은 글자. 예: '시스템 → 밝게'
  hint?: string
  run: () => void
}

const isOpen = defineModel<boolean>('open', { required: true })
const router = useRouter()
const modules = useModules()

const moveCommands: PaletteCommand[] = [
  { label: '홈', icon: House, run: () => void router.push('/') },
  { label: '상태', icon: Activity, run: () => void router.push('/status') },
  { label: '작업 기록', icon: History, run: () => void router.push('/jobs') },
]

const workCommands = computed<PaletteCommand[]>(() => [
  ...modules.flatMap((module) =>
    module.commands.map((command) => ({
      label: command.label,
      icon: command.icon,
      run: () => void router.push(command.to),
    })),
  ),
  {
    label: '테마 바꾸기',
    icon: SunMoon,
    hint: `${THEME_NAMES[theme.value]} → ${THEME_NAMES[nextTheme()]}`,
    run: cycleTheme,
  },
])

function run(command: PaletteCommand): void {
  isOpen.value = false
  command.run()
}

// ⌘K · Ctrl+K: 어디서든 팔레트를 열고 닫는다.
useEventListener(document, 'keydown', (event: KeyboardEvent) => {
  const isToggleKey = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k'
  if (!isToggleKey) return
  event.preventDefault()
  isOpen.value = !isOpen.value
})
</script>

<template>
  <CommandDialog v-model:open="isOpen">
    <CommandInput placeholder="화면 · 명령 검색">
      <span class="kbd">esc</span>
    </CommandInput>
    <CommandList>
      <CommandEmpty>결과 없음</CommandEmpty>
      <CommandGroup heading="이동">
        <CommandItem
          v-for="command in moveCommands"
          :key="command.label"
          :value="command.label"
          @select="run(command)"
        >
          <component :is="command.icon" />
          <span class="truncate">{{ command.label }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup heading="작업">
        <CommandItem
          v-for="command in workCommands"
          :key="command.label"
          :value="command.label"
          @select="run(command)"
        >
          <component :is="command.icon" />
          <span class="truncate">{{ command.label }}</span>
          <span v-if="command.hint" class="ml-auto text-meta text-muted-foreground">
            {{ command.hint }}
          </span>
        </CommandItem>
      </CommandGroup>
    </CommandList>
    <div class="flex h-9 items-center gap-4 border-t bg-muted/40 px-4 text-meta text-muted-foreground">
      <span class="flex items-center gap-1"><span class="kbd">↑</span><span class="kbd">↓</span> 고르기</span>
      <span class="flex items-center gap-1"><span class="kbd">↵</span> 열기</span>
    </div>
  </CommandDialog>
</template>
