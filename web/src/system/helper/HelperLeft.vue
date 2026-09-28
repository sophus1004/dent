<!--
  도우미가 끝난 뒤 남은 것 (세 묶음). 도우미 창의 단계 줄 아래에 붙는다(모든 모듈).
    머리        남은 것 n · 심각 · 주의
    상자 셋     AI로 고칠 수 있음(고르기 칸 · [모두 고르기]) · 직접 권장(고르기 칸, 뜻 판단) · AI로 못 고침(할 일만)
                줄: 고르기 칸 · 이름 · 작은 글 · 값 · →(그 문제만 보는 곳, 모듈이 정한다). 고른 줄에만 하는 일 · 어림 비용.
                줄에 더할 수 칸(item.adds, 예: 분류 라벨 균형의 라벨마다 새 문장 수)이 있으면 고른 줄 아래 표에서 고친다
                (이름 · 지금 · 추가 · 합계, 바닥에 item.adds_note). 고친 수는 [AI로 고치기]에 함께 보낸다.
    바닥 줄     고름 n · 어림 비용 · [AI로 고치기 n] (창 바닥에 붙는다)
  남은 것은 저장하지 않고 지금 진단으로 다시 센다: 처음 · 도우미가 데이터를 바꿀 때 · 주소가 바뀔 때 · 창으로 돌아올 때 · 15초마다.
  사람이 직접 고쳐 줄이 줄거나 사라지면 수 · 비용이 따라 바뀌고, 사라진 줄은 고름에서도 빠진다. 빈 상자는 보이지 않는다.
-->
<script setup lang="ts">
import { ArrowRight, Ban, Eye, ListChecks, Sparkles, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { GRADES } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'
import { helperChanged } from '@/system/helper/helperSignals'
import type { FixAdds, HelperApi } from '@/system/helper/useHelper'
import { errorMessage, isAbortError } from '@/system/http'
import type { HelperLeftAdd, HelperLeftItem } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Input } from '@/system/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

const props = defineProps<{
  datasetId: number
  api: HelperApi
  // 도는 중이거나 보내는 중이면 고치기를 막는다
  disabled: boolean
}>()

const emit = defineEmits<{
  fix: [keys: string[], adds: FixAdds]
}>()

// 남은 것을 다시 세는 간격(밀리초). 다른 탭에서 사람이 고친 것도 따라온다.
const REFRESH_MS = 15_000

const route = useRoute()
const items = ref<HelperLeftItem[] | null>(null)
const loadError = ref<string | null>(null)
const picked = ref<Set<string>>(new Set())
// 사람이 고친 더할 수 {검사: {칸: 수}}. 고치지 않은 칸은 모듈의 계획(item.adds의 add)대로.
const edits = ref<FixAdds>({})
let controller: AbortController | null = null

const groups = computed(() => {
  const all = items.value ?? []
  return {
    ai: all.filter((item) => item.group === 'ai'),
    direct: all.filter((item) => item.group === 'direct'),
    blocked: all.filter((item) => item.group === 'blocked'),
  }
})
const badCount = computed(() => (items.value ?? []).filter((item) => item.grade === 'bad').length)
const warnCount = computed(() => (items.value ?? []).filter((item) => item.grade === 'warn').length)
const chosen = computed(() => (items.value ?? []).filter((item) => picked.value.has(item.key)))
const tokens = computed(() => chosen.value.reduce((sum, item) => sum + item.tokens, 0))
const jevCalls = computed(() => chosen.value.reduce((sum, item) => sum + item.jev, 0))

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  try {
    const read = await props.api.getLeft(props.datasetId, current.signal)
    items.value = read.items
    loadError.value = null
    // 사람이 고쳐 사라진 줄 · AI로 못 고치게 된 줄은 고름에서 뺀다.
    const pickable = new Set(read.items.filter((item) => item.group !== 'blocked').map((item) => item.key))
    picked.value = new Set([...picked.value].filter((key) => pickable.has(key)))
  } catch (error) {
    if (!isAbortError(error)) loadError.value = errorMessage(error)
  }
}

function toggle(item: HelperLeftItem): void {
  if (item.group === 'blocked') return
  const next = new Set(picked.value)
  if (next.has(item.key)) next.delete(item.key)
  else next.add(item.key)
  picked.value = next
}

function pickAllAi(): void {
  picked.value = new Set([...picked.value, ...groups.value.ai.map((item) => item.key)])
}

function addOf(item: HelperLeftItem, cell: HelperLeftAdd): number {
  return edits.value[item.key]?.[cell.key] ?? cell.add
}

function setAdd(item: HelperLeftItem, cell: HelperLeftAdd, raw: string | number): void {
  const value = Math.min(Math.max(Math.round(Number(raw) || 0), 0), cell.max)
  edits.value = { ...edits.value, [item.key]: { ...edits.value[item.key], [cell.key]: value } }
}

function addTotal(item: HelperLeftItem): number {
  return item.adds.reduce((sum, cell) => sum + addOf(item, cell), 0)
}

function fix(): void {
  if (!chosen.value.length || props.disabled) return
  // 고친 줄만 모든 칸을 보낸다(고치지 않은 줄은 모듈이 계획대로 한다).
  const adds: FixAdds = {}
  for (const item of chosen.value) {
    if (!edits.value[item.key] || !item.adds.length) continue
    adds[item.key] = Object.fromEntries(item.adds.map((cell) => [cell.key, addOf(item, cell)]))
  }
  emit('fix', chosen.value.map((item) => item.key), adds)
  picked.value = new Set()
  edits.value = {}
}

function costText(item: HelperLeftItem): string {
  const parts = []
  if (item.tokens) parts.push(`LLM 약 ${fmt(item.tokens)}토큰`)
  if (item.jev) parts.push(`Jev ${fmt(item.jev)}`)
  return parts.length ? parts.join(' · ') : '규칙'
}

watch(() => props.datasetId, () => {
  items.value = null
  picked.value = new Set()
  void load()
}, { immediate: true })
watch(helperChanged, () => void load())
watch(() => route.fullPath, () => void load())
watch(() => props.disabled, (disabled, was) => {
  if (was && !disabled) void load()
})

const timer = setInterval(() => {
  if (!props.disabled && document.visibilityState === 'visible') void load()
}, REFRESH_MS)
const onFocus = () => void load()
window.addEventListener('focus', onFocus)
onBeforeUnmount(() => {
  clearInterval(timer)
  window.removeEventListener('focus', onFocus)
  controller?.abort()
})

// 상자 머리의 아이콘 · 이름 · 끝 글자
const BOXES = [
  { key: 'ai', icon: Sparkles, iconClass: 'text-primary', title: 'AI로 고칠 수 있음', note: '' },
  { key: 'direct', icon: Eye, iconClass: 'text-muted-foreground', title: '직접 권장', note: '뜻 판단' },
  { key: 'blocked', icon: Ban, iconClass: 'text-subtle-foreground', title: 'AI로 못 고침', note: '할 일만' },
] as const
</script>

<template>
  <section v-if="items" class="mt-4" data-helper-left>
    <div class="mb-2 flex items-center gap-1.5 text-ui">
      <ListChecks class="size-4 text-primary" />
      <b class="font-semibold">남은 것 {{ fmt(items.length) }}</b>
      <span v-if="items.length" class="text-meta text-muted-foreground">심각 {{ fmt(badCount) }} · 주의 {{ fmt(warnCount) }}</span>
    </div>
    <div v-if="loadError" class="flex items-center gap-1.5 text-ui text-danger-ink" role="alert">
      <TriangleAlert class="size-3.5 text-danger" />{{ loadError }}
    </div>
    <div v-else-if="!items.length" class="rounded-[10px] border border-dashed px-3 py-2.5 text-ui text-muted-foreground">
      없음
    </div>
    <template v-for="box in BOXES" :key="box.key">
      <div
        v-if="groups[box.key].length"
        class="mb-2 overflow-hidden rounded-[11px] border"
        :class="box.key === 'blocked' ? 'bg-muted/30' : 'bg-card'"
        :data-left-box="box.key"
      >
        <div class="flex min-h-9 items-center gap-1.5 border-b px-3 text-ui">
          <component :is="box.icon" class="size-3.5" :class="box.iconClass" />
          <b class="font-semibold">{{ box.title }}</b>
          <span class="text-meta font-semibold text-muted-foreground tabular-nums">{{ groups[box.key].length }}</span>
          <button
            v-if="box.key === 'ai'"
            type="button"
            class="ml-auto text-meta font-semibold text-accent-foreground hover:underline"
            data-action="left-pick-all"
            @click="pickAllAi"
          >모두 고르기</button>
          <span v-else class="ml-auto text-meta text-muted-foreground">{{ box.note }}</span>
        </div>
        <div
          v-for="item in groups[box.key]"
          :key="item.key"
          class="border-b px-3 py-2 last:border-b-0"
          :class="picked.has(item.key) && 'bg-accent/50'"
          :data-left-item="item.key"
        >
          <div class="grid grid-cols-[16px_minmax(0,1fr)_auto_26px] items-center gap-2">
            <input
              v-if="item.group !== 'blocked'"
              type="checkbox"
              class="size-4 accent-[var(--primary)]"
              :checked="picked.has(item.key)"
              :aria-label="item.name"
              @change="toggle(item)"
            />
            <Ban v-else class="size-3.5 text-subtle-foreground" />
            <button type="button" class="min-w-0 text-left" :class="item.group === 'blocked' && 'cursor-default'" @click="toggle(item)">
              <span class="flex items-center gap-1.5 text-ui font-semibold">
                {{ item.name }}
                <span
                  v-if="item.group === 'blocked' && item.action"
                  class="inline-flex h-[17px] items-center rounded-[5px] bg-muted px-1.5 text-[10.5px] font-semibold text-muted-foreground"
                >{{ item.action }}</span>
              </span>
              <small class="block truncate text-meta text-muted-foreground" :title="item.sub">{{ item.sub }}</small>
            </button>
            <span class="text-right whitespace-nowrap tabular-nums">
              <b class="text-[15px] font-[650]" :class="GRADES[item.grade].textClass">{{ item.value }}</b>
              <small class="ml-0.5 text-meta text-muted-foreground">{{ item.unit }}</small>
            </span>
            <Tooltip>
              <TooltipTrigger as-child>
                <Button variant="outline" size="icon-sm" class="size-[26px]" as-child>
                  <RouterLink :to="api.leftRoute(datasetId, item)" :aria-label="`${item.name} 보기`"><ArrowRight /></RouterLink>
                </Button>
              </TooltipTrigger>
              <TooltipContent>보기</TooltipContent>
            </Tooltip>
          </div>
          <div v-if="picked.has(item.key) && item.how" class="mt-1 ml-6 flex flex-wrap items-center gap-x-2 text-meta">
            <span class="text-accent-foreground">↳ {{ item.how }}</span>
            <span class="ml-auto text-muted-foreground tabular-nums">{{ costText(item) }}</span>
          </div>
          <table v-if="picked.has(item.key) && item.adds.length" class="mt-1.5 ml-6 w-[calc(100%-24px)] text-ui" data-left-adds>
            <thead>
              <tr class="text-meta text-muted-foreground">
                <th class="py-0.5 text-left font-medium" />
                <th class="py-0.5 text-right font-medium">지금</th>
                <th class="py-0.5 text-right font-medium">추가</th>
                <th class="py-0.5 text-right font-medium">합계</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cell in item.adds" :key="cell.key" class="border-t" :data-left-add="cell.key">
                <td class="max-w-[140px] truncate py-1" :title="cell.name">{{ cell.name }}</td>
                <td class="py-1 text-right text-muted-foreground tabular-nums">{{ fmt(cell.now) }}</td>
                <td class="py-1 text-right">
                  <Input
                    type="number"
                    min="0"
                    :max="cell.max"
                    class="ml-auto h-7 w-16 text-right tabular-nums"
                    :model-value="addOf(item, cell)"
                    :aria-label="`${cell.name} 추가`"
                    @update:model-value="setAdd(item, cell, $event)"
                  />
                </td>
                <td class="py-1 text-right tabular-nums" :class="addOf(item, cell) ? 'font-semibold' : 'text-muted-foreground'">
                  {{ fmt(cell.now + addOf(item, cell)) }}
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="border-t text-meta">
                <td colspan="2" class="py-1 text-muted-foreground">{{ item.adds_note }}</td>
                <td class="py-1 text-right font-semibold text-accent-foreground tabular-nums">+{{ fmt(addTotal(item)) }}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </template>
    <div
      v-if="groups.ai.length || groups.direct.length"
      class="sticky bottom-0 flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-[11px] border bg-card px-3 py-2 text-ui shadow-[0_-8px_16px_-12px_rgba(17,17,24,0.25)]"
      data-left-fix
    >
      <b class="font-semibold" :class="chosen.length ? 'text-accent-foreground' : 'text-muted-foreground'">고름 {{ fmt(chosen.length) }}</b>
      <span class="text-meta text-muted-foreground tabular-nums">
        <template v-if="chosen.length">
          LLM 약 {{ fmt(tokens) }}토큰<template v-if="jevCalls"> · Jev {{ fmt(jevCalls) }}</template>
        </template>
        <template v-else>—</template>
      </span>
      <Button
        type="button"
        size="sm"
        class="ml-auto"
        :disabled="!chosen.length || disabled"
        data-action="helper-fix"
        @click="fix"
      >
        <Sparkles />AI로 고치기<span class="ml-0.5 rounded-full bg-primary-foreground/20 px-1.5 text-[11px] tabular-nums">{{ chosen.length }}</span>
      </Button>
    </div>
  </section>
</template>
