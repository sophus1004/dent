<!--
  데이터셋 설정 창. 데이터셋 머리의 [⋯] → [설정]으로 연다.
    학습할 모델   비우면 연결된 임베딩 모델 (오답 찾기 · 기준 검색이 이 모델로 돈다)
    길이          질의 최대 토큰 · 문서 최대 토큰 (긴 문서 · 짧은 · 긴 질의 검사와 나누기 기준)
    학습 글       구획 제목 붙이기 (나눈 청크의 머리말에 구획 경로)
    오답          질의마다 오답 수 (group = 오답 + 1) · 오답 풀 · 찾을 순위 범위 · 거짓 오답 경계(정답 유사도의 배)
    오답 훑기     순위 묶음 × 문턱의 거짓 오답 비율 표 (Jev). 칸을 누르면 그 순위 시작 · 문턱을 폼에 넣는다. [훑기]로 다시 잰다
    질의 만들기   되찾기 거르기 · 쉬운 쌍 상한(%)
  PATCH /api/v1/retrieval/datasets/{id}/settings. 저장하면 saved를 알린다(머리 · 진단이 다시 읽는다).
-->
<script setup lang="ts">
import { LoaderCircle, ScanSearch, Settings2, TriangleAlert } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'

import { errorMessage } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Input } from '@/system/ui/input'

import { scanNegatives, updateSettings } from '@/modules/retrieval/api'
import type { NegativeScanCellRead, NegativeScanRead, SettingsRead } from '@/modules/retrieval/types'

const props = defineProps<{
  datasetId: number
  settings: SettingsRead
}>()

const emit = defineEmits<{
  saved: []
}>()

const open = defineModel<boolean>('open', { required: true })

const form = reactive({
  target_model: '',
  query_max_tokens: 64,
  doc_max_tokens: 512,
  negatives: 7,
  negative_pool: 20,
  mine_rank_from: 10,
  mine_rank_to: 200,
  mine_margin: 0.95,
  section_header: true,
  round_trip: true,
  easy_pair_percent: 30,
})
const saving = ref(false)
const scanning = ref(false)
const error = ref<string | null>(null)

// 거짓 오답 비율의 칸 색 경계 (보여 주기만: 5% 미만 초록 · 15% 미만 호박 · 그 위 빨강)
const RATE_GOOD_BELOW = 0.05
const RATE_WARN_BELOW = 0.15

const scan = computed<NegativeScanRead | null>(() => {
  const value = props.settings.negative_scan as NegativeScanRead
  return value && Array.isArray(value.cells) && value.cells.length ? value : null
})
const scanBuckets = computed(() => {
  const seen = new Map<string, { from: number; to: number }>()
  for (const cell of scan.value?.cells ?? []) seen.set(`${cell.rank_from}-${cell.rank_to}`, { from: cell.rank_from, to: cell.rank_to })
  return [...seen.values()]
})
const scanMargins = computed(() => [...new Set((scan.value?.cells ?? []).map((cell) => cell.margin))])

watch(open, (isOpen) => {
  if (!isOpen) return
  const { settings } = props
  Object.assign(form, {
    target_model: settings.target_model ?? '',
    query_max_tokens: settings.query_max_tokens,
    doc_max_tokens: settings.doc_max_tokens,
    negatives: settings.negatives,
    negative_pool: settings.negative_pool,
    mine_rank_from: settings.mine_rank_from,
    mine_rank_to: settings.mine_rank_to,
    mine_margin: settings.mine_margin,
    section_header: settings.section_header,
    round_trip: settings.round_trip,
    easy_pair_percent: Math.round(settings.easy_pair_cap * 100),
  })
  error.value = null
  scanning.value = false
})

// 훑기 작업이 끝나 설정을 다시 읽으면(새 결과) 도는 표시를 끈다.
watch(
  () => props.settings.negative_scan,
  () => {
    scanning.value = false
  },
)

function cell(from: number, margin: number): NegativeScanCellRead | undefined {
  return scan.value?.cells.find((item) => item.rank_from === from && item.margin === margin)
}

function rateTone(item: NegativeScanCellRead | undefined): string {
  if (!item || !item.asked) return 'text-subtle-foreground'
  const rate = item.false_negatives / item.asked
  if (rate < RATE_GOOD_BELOW) return 'text-success-ink'
  if (rate < RATE_WARN_BELOW) return 'text-warning-ink'
  return 'text-danger-ink'
}

function pick(from: number, margin: number): void {
  form.mine_rank_from = from
  form.mine_margin = margin
}

async function runScan(): Promise<void> {
  scanning.value = true
  error.value = null
  try {
    await scanNegatives(props.datasetId)
  } catch (failure) {
    error.value = errorMessage(failure)
    scanning.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  const { easy_pair_percent: easyPercent, ...rest } = form
  try {
    await updateSettings(props.datasetId, {
      ...rest,
      target_model: form.target_model.trim(),
      easy_pair_cap: easyPercent / 100,
    })
    open.value = false
    emit('saved')
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    saving.value = false
  }
}

const ROW = 'grid grid-cols-[120px_minmax(0,1fr)] items-center gap-x-4 gap-y-1'
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="max-h-[calc(100vh-48px)] w-[min(600px,calc(100vw-32px))] max-w-none gap-0 overflow-auto bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="retrieval-settings"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
        <Settings2 class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">설정</DialogTitle>
        <DialogDescription class="sr-only">학습할 모델 · 길이 · 학습 글 · 오답 · 질의 만들기</DialogDescription>
      </DialogHeader>

      <form class="space-y-3.5 px-5 py-4 text-ui" @submit.prevent="save">
        <label :class="ROW">
          <span class="font-[550]">학습할 모델</span>
          <Input v-model="form.target_model" placeholder="연결된 임베딩" class="font-mono" data-field="target-model" />
        </label>
        <div :class="ROW">
          <span class="font-[550]">최대 토큰</span>
          <span class="flex items-center gap-2">
            질의 <Input v-model.number="form.query_max_tokens" type="number" min="8" max="8192" class="w-20" />
            문서 <Input v-model.number="form.doc_max_tokens" type="number" min="8" max="8192" class="w-24" />
          </span>
        </div>
        <label :class="ROW">
          <span class="font-[550]">학습 글</span>
          <span class="flex items-center gap-2">
            <input v-model="form.section_header" type="checkbox" class="size-4 accent-[var(--primary)]" data-field="section-header" />
            구획 제목 붙이기
            <span class="text-muted-foreground">제목 › 머리말 칸 › 구획</span>
          </span>
        </label>

        <div class="border-t pt-3.5" />
        <div :class="ROW">
          <span class="font-[550]">오답 수</span>
          <span class="flex items-center gap-2">
            질의마다 <Input v-model.number="form.negatives" type="number" min="1" max="50" class="w-20" data-field="negatives" />
            <span class="text-muted-foreground">group {{ form.negatives + 1 }}</span>
          </span>
        </div>
        <div :class="ROW">
          <span class="font-[550]">오답 풀</span>
          <span class="flex items-center gap-2">
            질의마다 <Input v-model.number="form.negative_pool" type="number" min="1" max="100" class="w-20" data-field="negative-pool" />
            <span class="text-muted-foreground">jsonl 전체 · 학습기가 {{ form.negatives }}개씩</span>
          </span>
        </div>
        <div :class="ROW">
          <span class="font-[550]">찾을 순위</span>
          <span class="flex items-center gap-2">
            <Input v-model.number="form.mine_rank_from" type="number" min="1" max="1000" class="w-20" data-field="rank-from" />
            ~
            <Input v-model.number="form.mine_rank_to" type="number" min="1" max="1000" class="w-20" />위
          </span>
        </div>
        <div :class="ROW">
          <span class="font-[550]">거짓 오답 경계</span>
          <span class="flex items-center gap-2">
            정답 유사도 ×
            <Input v-model.number="form.mine_margin" type="number" min="0.5" max="1" step="0.01" class="w-20" data-field="margin" />
            <span class="text-muted-foreground">넘으면 뺌</span>
          </span>
        </div>
        <div class="grid grid-cols-[120px_minmax(0,1fr)] items-start gap-x-4">
          <span class="pt-1 font-[550]">오답 훑기</span>
          <div class="grid gap-2" data-negative-scan>
            <table v-if="scan" class="w-full border-collapse text-meta tabular-nums">
              <thead>
                <tr class="text-muted-foreground">
                  <th class="py-1 pr-2 text-left font-[550]">순위</th>
                  <th v-for="margin in scanMargins" :key="margin" class="px-1 py-1 text-right font-[550]">×{{ margin.toFixed(2) }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="bucket in scanBuckets" :key="bucket.from" class="border-t">
                  <td class="py-1 pr-2 whitespace-nowrap text-muted-foreground">{{ bucket.from }}~{{ bucket.to }}</td>
                  <td v-for="margin in scanMargins" :key="margin" class="px-0.5 py-0.5 text-right">
                    <button
                      type="button"
                      class="w-full rounded-md px-1 py-0.5 text-right hover:bg-muted"
                      :class="[rateTone(cell(bucket.from, margin)), form.mine_rank_from === bucket.from && form.mine_margin === margin ? 'ring-1 ring-primary' : '']"
                      :title="`거짓 오답 ${cell(bucket.from, margin)?.false_negatives ?? 0} / ${cell(bucket.from, margin)?.asked ?? 0} · 남음 ${cell(bucket.from, margin)?.kept ?? 0}`"
                      :data-scan-cell="`${bucket.from}-${margin}`"
                      @click="pick(bucket.from, margin)"
                    >
                      {{ cell(bucket.from, margin)?.asked ? `${Math.round((cell(bucket.from, margin)!.false_negatives / cell(bucket.from, margin)!.asked) * 100)}%` : '—' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <span class="flex items-center gap-2 text-muted-foreground">
              <template v-if="scan">질의 {{ scan.queries }} · 거짓 오답 비율 · 칸 → 시작 순위 · 경계</template>
              <template v-else>없음</template>
              <Button type="button" size="sm" variant="outline" class="ml-auto" :disabled="scanning" data-action="scan-negatives" @click="runScan">
                <LoaderCircle v-if="scanning" class="animate-spin" /><ScanSearch v-else />{{ scanning ? '훑는 중' : '훑기' }}
              </Button>
            </span>
          </div>
        </div>

        <div class="border-t pt-3.5" />
        <label :class="ROW">
          <span class="font-[550]">질의 만들기</span>
          <span class="flex items-center gap-2">
            <input v-model="form.round_trip" type="checkbox" class="size-4 accent-[var(--primary)]" data-field="round-trip" />
            되찾기 거르기
            <span class="text-muted-foreground">10위 안</span>
          </span>
        </label>
        <div :class="ROW">
          <span class="font-[550]">쉬운 쌍 상한</span>
          <span class="flex items-center gap-2">
            <Input v-model.number="form.easy_pair_percent" type="number" min="0" max="100" class="w-20" data-field="easy-pair-cap" />%
          </span>
        </div>

        <div v-if="error" class="flex items-start gap-2" role="alert">
          <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ error }}</span>
        </div>

        <div class="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" @click="open = false">취소</Button>
          <Button type="submit" :disabled="saving" data-action="save-settings">
            <LoaderCircle v-if="saving" class="animate-spin" />저장
          </Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>
</template>
