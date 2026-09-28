<!--
  도우미 사건 하나(단계 안쪽). 종류마다 다른 모양:
    say          에이전트의 말. 스스로 묻는 말(ask)은 보라 '?' 표시 · 보라 굵은 글, 답은 점 · 보통 글
    jev          Jev 판정 표: 문장(지금 라벨) · 고른 라벨 · 확률 막대 · 바꿈/LLM에/버림
    llm          LLM이 직접 본 문장: 문장(사실) · → 라벨(보류 · 유지 · 빼기) · 까닭
    change       바꾼 카드: 동작 · n건 · 도구 · 보기 문장 · ↶ 되돌리기(도는 중에는 막힘, 되돌렸으면 '되돌림')
    result       검사 값: 등급 · 이름 · 처음 → 지금 · 등급 이름
    hold         보류 · 사람 확인: 문장 · 까닭 · [정하기](데이터 탭에서 그 문장을 찾는다)
    permission   허락 카드: 제목(payload.title, 없으면 새 문장 만들기) · 주의(payload.alert: 글 · 작은 글 · [동작], 예: 긴 문서 ·
                 [먼저 나누기]) · 사실 줄(payload.facts [[이름, 값]], 없으면 분류의 무엇을 · 왜 · 비용)
                 · 남은 문제(payload.checks, 앞 단계에 남은 주의) · 실행 설정(payload.settings 요약, 읽기만 · [설정]으로 카드를 연다)
                 · [허락하고 시작] [건너뛰기]
    notice       알림 한 줄 (멈춤 · 실패 · 되돌림). payload.checks가 있으면 아래에 남은 문제(등급 · 검사 · 값)를 줄마다
                 (막힘 = 문에 걸림 · 끝의 남은 문제)
    report       보고 (실행 · AI로 고치기 끝): 종합 처음 → 끝 · 점수 · 고친 검사(처음 → 끝 · 등급) · 바꿈 · 비용 · [내려받기](md)
  에이전트의 말만 문장이다(도우미 창의 예외). 나머지는 표 · 칩 · 숫자다.
-->
<script setup lang="ts">
import {
  ArrowRight,
  Bot,
  Check,
  Download,
  EyeOff,
  FileChartColumn,
  FilePlus2,
  Gavel,
  Hand,
  Info,
  Pencil,
  Pin,
  Scissors,
  SlidersHorizontal,
  Tag,
  TriangleAlert,
  Undo2,
} from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

import { fmt } from '@/system/format'
import type { HelperEventRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { GRADES, type Grade } from '@/system/diagnosis/grades'

const props = defineProps<{
  event: HelperEventRead
  datasetId: number
  // 보류 카드의 [정하기]: 데이터 탭에서 그 글을 찾는 주소 (모듈이 준다)
  holdRoute: (datasetId: number, text: string) => RouteLocationRaw
  // 이 바꾼 카드를 되돌렸는지
  undone: boolean
  // 되돌릴 수 있는지 (도는 중이 아니고 보내는 중이 아님)
  canUndo: boolean
  // 허락을 지금 기다리는지 (허락 카드의 버튼을 켠다)
  asking: boolean
  // 보내는 중
  busy: boolean
}>()

const emit = defineEmits<{
  undo: [eventId: number]
  answer: [approve: boolean, action?: string]
  // 허락 카드의 [설정]: 실행 설정 카드를 연다
  settings: []
}>()

// 표 한 줄 (jev · llm)
interface Row {
  text: string
  from?: string
  choice?: string
  prob?: number
  decision?: string
  to?: string
  why?: string
}

const payload = computed(() => props.event.payload as Record<string, unknown>)
const rows = computed(() => (payload.value.rows as Row[] | undefined) ?? [])
const text = (key: string) => String(payload.value[key] ?? '')
const samples = computed(() => (payload.value.samples as string[] | undefined) ?? [])
const holdItems = computed(() => (payload.value.items as { text: string; facts?: string; why: string }[] | undefined) ?? [])
const targets = computed(
  () => (payload.value.targets as { label: string; count: number; now?: number }[] | undefined) ?? [],
)
// 허락 카드의 사실 줄 [[이름, 값]] (모듈이 정한다). 없으면 분류의 새 문장 계획 모양으로 그린다.
const permissionFacts = computed(() => (payload.value.facts as [string, string][] | undefined) ?? [])
// 허락 카드의 주의 (예: 나누기를 건너뛰어 긴 문서가 남음 · [먼저 나누기])
const permissionAlert = computed(
  () => (payload.value.alert as { text: string; sub?: string; action?: string; label?: string } | undefined) ?? null,
)
// 보고의 모양 (백엔드 Agent.write_report)
interface ReportPayload {
  title: string
  overall: [string, string]
  overall_grade: [Grade, Grade]
  counts: [{ bad: number; warn: number }, { bad: number; warn: number }]
  kpi: { name: string; from: string; to: string }[]
  rows: { name: string; from: string; to: string; grade: Grade }[]
  more: number
  changed: string
  cost: { llm: number; jev: number; time: string }
}
const report = computed(() => (props.event.kind === 'report' ? (payload.value as unknown as ReportPayload) : null))

/** 보고를 md 파일로 내려받는다(화면의 보고와 같은 수). */
function downloadReport(): void {
  const data = report.value
  if (!data) return
  const lines = [
    `# ${data.title}`,
    '',
    `- 종합: ${data.overall[0]} → ${data.overall[1]} (심각 ${data.counts[0].bad} → ${data.counts[1].bad} · 주의 ${data.counts[0].warn} → ${data.counts[1].warn})`,
    ...data.kpi.map((item) => `- ${item.name}: ${item.from} → ${item.to}`),
    `- 바꿈: ${data.changed || '—'}`,
    `- 비용: LLM ${data.cost.llm}토큰 · Jev ${data.cost.jev} · ${data.cost.time}`,
    '',
    '| 검사 | 처음 | 끝 |',
    '|---|---|---|',
    ...data.rows.map((row) => `| ${row.name} | ${row.from} | ${row.to} |`),
  ]
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/markdown' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `dent-보고-${props.event.id}.md`
  link.click()
  URL.revokeObjectURL(url)
}

// 남은 문제 (막힘 · 끝 알림 · 허락 카드). 심각이 앞에 온다.
const openChecks = computed(
  () => (payload.value.checks as { name: string; value: string; grade: Grade }[] | undefined) ?? [],
)

const DECISION_WORDS: Record<string, { word: string; tone: string }> = {
  apply: { word: '바꿈', tone: 'text-success-ink bg-success/10' },
  llm: { word: 'LLM에', tone: 'text-warning-ink bg-warning/12' },
  drop: { word: '버림', tone: 'text-danger-ink bg-danger/10' },
}

// 바꾼 카드의 동작 이름 → 아이콘 (모듈이 쓰는 동작 이름. 모르는 이름은 빼기 아이콘)
const ACTION_ICONS: Record<string, typeof EyeOff> = {
  '라벨 바꾸기': Tag,
  '새 문장 더하기': FilePlus2,
  '새 질의 더하기': FilePlus2,
  '문서 나누기': Scissors,
  '정답 붙이기': Check,
  '오답 붙이기': Check,
  '판정 바꾸기': Gavel,
  '글자 정리': Pencil,
  '상용구 지우기': Pencil,
  '질의 고치기': Pencil,
}

const changeIcon = computed(() => {
  if (payload.value.keep) return Pin
  return ACTION_ICONS[text('action')] ?? EyeOff
})

const grade = computed(() => (text('grade') || 'good') as Grade)

const NOTICE_TONE: Record<string, string> = {
  bad: 'text-danger-ink',
  warn: 'text-warning-ink',
  info: 'text-muted-foreground',
}

// 표의 칸 (문장은 한 줄에서 자르고, 올리면 전체)
const CELL = 'border-b px-2.5 py-1.5 align-middle group-last:border-b-0'
</script>

<template>
  <!-- 에이전트의 말 -->
  <div v-if="event.kind === 'say'" class="my-1.5 grid grid-cols-[18px_minmax(0,1fr)] gap-1.5" data-event="say">
    <span
      v-if="payload.ask"
      class="mt-px grid size-[18px] place-items-center rounded-full bg-accent text-[11px] font-bold text-accent-foreground"
    >?</span>
    <span v-else class="grid size-[18px] place-items-center"><span class="size-[5px] rounded-full bg-primary/55" /></span>
    <p class="m-0 text-body leading-[1.55]" :class="payload.ask ? 'font-semibold text-primary' : ''">{{ text('text') }}</p>
  </div>

  <!-- Jev 판정 · LLM이 본 문장 -->
  <div v-else-if="event.kind === 'jev' || event.kind === 'llm'" class="my-2 ml-6 overflow-hidden rounded-[9px] border bg-card" :data-event="event.kind">
    <div class="flex h-8 items-center gap-1.5 border-b bg-muted/55 px-2.5 text-ui">
      <component :is="event.kind === 'jev' ? Gavel : Bot" class="size-3.5" :class="event.kind === 'llm' ? 'text-primary' : 'text-muted-foreground'" />
      <b class="font-semibold">{{ event.kind === 'jev' ? 'Jev 판정' : 'LLM' }}</b>
      <span class="ml-auto truncate text-meta text-muted-foreground">{{ text('meta') }}</span>
    </div>
    <table class="w-full table-fixed border-collapse text-ui">
      <tbody>
        <tr v-for="(row, index) in rows" :key="index" class="group">
          <td :class="CELL">
            <span class="block truncate font-[550]" :title="row.text">“{{ row.text }}”</span>
            <small v-if="row.from" class="block truncate text-meta text-muted-foreground" :title="row.from">{{ row.from }}</small>
          </td>
          <template v-if="event.kind === 'jev'">
            <td :class="CELL" class="w-[72px] font-semibold whitespace-nowrap">{{ row.choice }}</td>
            <td :class="CELL" class="w-[88px] whitespace-nowrap">
              <span class="mr-1.5 inline-block h-1.5 w-10 overflow-hidden rounded-full bg-muted align-middle">
                <span
                  class="block h-full rounded-full"
                  :class="row.decision === 'apply' ? 'bg-success' : row.decision === 'drop' ? 'bg-danger' : 'bg-warning'"
                  :style="{ width: `${Math.round((row.prob ?? 0) * 100)}%` }"
                />
              </span>
              <span class="tabular-nums">{{ (row.prob ?? 0).toFixed(2) }}</span>
            </td>
            <td :class="CELL" class="w-[58px] text-right">
              <span
                class="inline-flex h-[18px] items-center rounded-[5px] px-1.5 text-[11px] font-semibold whitespace-nowrap"
                :class="DECISION_WORDS[row.decision ?? 'llm']?.tone"
              >{{ DECISION_WORDS[row.decision ?? 'llm']?.word }}</span>
            </td>
          </template>
          <template v-else>
            <td :class="CELL" class="w-[64px] font-semibold whitespace-nowrap">
              <span v-if="row.to === '' || row.to === null" class="text-warning-ink">보류</span>
              <span v-else>{{ row.to }}</span>
            </td>
            <td :class="CELL" class="w-[36%] text-meta text-muted-foreground">
              <span class="line-clamp-2" :title="row.why">{{ row.why }}</span>
            </td>
          </template>
        </tr>
      </tbody>
    </table>
    <div v-if="text('more')" class="border-t px-2.5 py-1 text-meta text-muted-foreground">{{ text('more') }}</div>
  </div>

  <!-- 바꾼 카드 -->
  <div
    v-else-if="event.kind === 'change'"
    class="my-2 ml-6 rounded-[9px] px-2.5 py-1.5"
    :class="payload.keep ? 'bg-muted/60 shadow-[inset_0_0_0_1px_var(--border)]' : 'bg-accent/70 shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--primary)_22%,transparent)]'"
    data-event="change"
  >
    <div class="flex min-h-[22px] items-center gap-1.5 text-ui">
      <component :is="changeIcon" class="size-3.5" :class="payload.keep ? 'text-muted-foreground' : 'text-primary'" />
      <b class="font-semibold">{{ text('action') }}</b>
      <b class="font-bold tabular-nums" :class="payload.keep ? 'text-foreground' : 'text-primary'">{{ fmt(Number(payload.count ?? 0)) }}건</b>
      <span class="truncate text-meta text-muted-foreground">{{ text('tool') }}</span>
      <span v-if="undone" class="ml-auto inline-flex h-5 items-center rounded-md px-1.5 text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]">되돌림</span>
      <Tooltip v-else-if="!payload.keep">
        <TooltipTrigger as-child>
          <Button
            type="button"
            variant="quiet"
            size="icon-sm"
            class="ml-auto size-6"
            :disabled="!canUndo"
            aria-label="되돌리기"
            data-action="helper-undo-card"
            @click="emit('undo', event.id)"
          >
            <Undo2 />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{{ canUndo ? '되돌리기' : '멈춘 뒤 되돌리기' }}</TooltipContent>
      </Tooltip>
    </div>
    <div v-if="samples.length" class="mt-0.5 mb-0.5 ml-5 grid gap-px text-meta text-muted-foreground">
      <span v-for="(sample, index) in samples" :key="index" class="truncate" :title="sample">“{{ sample }}”</span>
    </div>
  </div>

  <!-- 검사 값이 바뀜 -->
  <div
    v-else-if="event.kind === 'result'"
    class="my-2 mb-3.5 ml-6 flex items-center gap-1.5 rounded-lg bg-muted/55 px-2.5 py-1.5 text-ui"
    data-event="result"
  >
    <component :is="GRADES[grade].icon" class="size-3.5" :class="GRADES[grade].iconClass" :stroke-width="2.2" />
    <b class="font-semibold">{{ text('check') }}</b>
    <span class="text-muted-foreground line-through">{{ text('from') }}</span>
    <ArrowRight class="size-3 text-muted-foreground" />
    <b class="font-semibold" :class="GRADES[grade].textClass">{{ text('to') }}</b>
    <span class="text-meta font-semibold" :class="GRADES[grade].textClass">{{ GRADES[grade].name }}</span>
    <span v-if="text('note')" class="truncate text-meta text-muted-foreground">· {{ text('note') }}</span>
  </div>

  <!-- 보류 · 사람 확인 -->
  <div
    v-else-if="event.kind === 'hold'"
    class="my-2 ml-6 rounded-[9px] bg-warning/8 px-2.5 py-2 text-ui shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--warning)_45%,transparent)]"
    data-event="hold"
  >
    <div class="flex items-center gap-1.5">
      <Hand class="size-3.5 text-warning" />
      <b class="font-semibold text-warning-ink">보류 · 사람 확인</b>
      <span class="text-meta text-muted-foreground">{{ holdItems.length }}건</span>
    </div>
    <div v-for="(item, index) in holdItems" :key="index" class="mt-1.5 flex items-center gap-2">
      <span class="min-w-0 flex-1">
        <span class="block truncate font-[550]" :title="item.text">“{{ item.text }}”</span>
        <small class="block truncate text-meta text-muted-foreground">{{ item.why }}{{ item.facts ? ` · ${item.facts}` : '' }}</small>
      </span>
      <Button variant="outline" size="xs" as-child>
        <RouterLink :to="holdRoute(datasetId, item.text)">
          정하기<ArrowRight />
        </RouterLink>
      </Button>
    </div>
  </div>

  <!-- 허락 카드 (새 글 만들기 · 문서 나누기 …) -->
  <div
    v-else-if="event.kind === 'permission'"
    class="my-2.5 ml-6 overflow-hidden rounded-[10px] bg-card shadow-[0_0_0_1.5px_color-mix(in_oklab,var(--warning)_60%,transparent),var(--shadow-card)]"
    data-event="permission"
  >
    <div class="flex items-center gap-1.5 border-b bg-warning/7 px-3 py-2 text-body">
      <FilePlus2 class="size-4 text-warning" />
      <b class="font-semibold">{{ text('title') || '새 문장 만들기' }}</b>
      <span class="ml-auto inline-flex h-5 items-center rounded-md px-[7px] text-meta font-semibold text-warning-ink shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--warning)_50%,transparent)]">허락 필요</span>
    </div>
    <div
      v-if="permissionAlert"
      class="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-b bg-warning/10 px-3 py-2 text-ui"
      data-permission-alert
    >
      <TriangleAlert class="size-3.5 text-warning" />
      <b class="font-semibold text-warning-ink">{{ permissionAlert.text }}</b>
      <span v-if="permissionAlert.sub" class="text-muted-foreground">{{ permissionAlert.sub }}</span>
      <Button
        v-if="asking && permissionAlert.action"
        type="button"
        variant="outline"
        size="xs"
        class="ml-auto"
        :disabled="busy"
        data-action="helper-alert-action"
        @click="emit('answer', true, permissionAlert.action)"
      >
        <Scissors />{{ permissionAlert.label }}
      </Button>
    </div>
    <dl v-if="permissionFacts.length" class="m-0 grid grid-cols-[56px_minmax(0,1fr)] gap-x-2.5 gap-y-1 px-3 py-2.5 text-ui">
      <template v-for="[name, value] in permissionFacts" :key="name">
        <dt class="text-muted-foreground">{{ name }}</dt>
        <dd class="m-0">{{ value }}</dd>
      </template>
    </dl>
    <dl v-else class="m-0 grid grid-cols-[48px_minmax(0,1fr)] gap-x-2.5 gap-y-1 px-3 py-2.5 text-ui">
      <dt class="text-muted-foreground">무엇을</dt>
      <dd class="m-0 flex flex-wrap gap-x-2">
        <span v-for="target in targets" :key="target.label">
          <b class="font-semibold">{{ target.label }}</b> +{{ fmt(target.count) }}<span v-if="target.now !== undefined" class="text-muted-foreground"> ({{ fmt(target.now) }})</span>
        </span>
      </dd>
      <dt class="text-muted-foreground">왜</dt>
      <dd class="m-0">{{ text('reason') }}</dd>
      <dt class="text-muted-foreground">어떻게</dt>
      <dd class="m-0">LLM이 만듦 → 같은 문장 · 라벨 문장 · 새 문장끼리 근접 거르기 → Jev 라벨 확인(≥ 0.8)</dd>
      <dt class="text-muted-foreground">비용</dt>
      <dd class="m-0">{{ text('cost') }}</dd>
    </dl>
    <div v-if="openChecks.length" class="grid gap-1 border-t px-3 py-2 text-ui" data-permission-checks>
      <span class="text-meta text-muted-foreground">남은 문제</span>
      <span v-for="check in openChecks" :key="check.name" class="flex items-center gap-1.5">
        <component :is="GRADES[check.grade].icon" class="size-3.5" :class="GRADES[check.grade].iconClass" :stroke-width="2.2" />
        <span class="font-[550]">{{ check.name }}</span>
        <b class="font-semibold" :class="GRADES[check.grade].textClass">{{ check.value }}</b>
      </span>
    </div>
    <div v-if="text('settings')" class="flex items-center gap-1.5 border-t px-3 py-2 text-ui" data-permission-settings>
      <span class="text-muted-foreground">실행 설정</span>
      <b class="min-w-0 truncate font-semibold" :title="text('settings')">{{ text('settings') }}</b>
      <button
        v-if="asking"
        type="button"
        class="ml-auto inline-flex shrink-0 items-center gap-1 text-meta font-semibold text-accent-foreground hover:underline"
        data-action="helper-open-settings"
        @click="emit('settings')"
      >
        <SlidersHorizontal class="size-3" />설정
      </button>
    </div>
    <div v-if="asking" class="flex gap-1.5 px-3 pt-1 pb-3">
      <Button type="button" size="sm" :disabled="busy" data-action="helper-approve" @click="emit('answer', true)">
        <Check />허락하고 시작
      </Button>
      <Button type="button" variant="quiet" size="sm" :disabled="busy" data-action="helper-decline" @click="emit('answer', false)">
        건너뛰기
      </Button>
    </div>
  </div>

  <!-- 보고 -->
  <div
    v-else-if="event.kind === 'report' && report"
    class="my-2.5 ml-6 overflow-hidden rounded-[10px] border bg-card"
    data-event="report"
  >
    <div class="flex min-h-9 items-center gap-1.5 border-b bg-muted/45 py-1 pr-1.5 pl-3 text-body">
      <FileChartColumn class="size-4 text-primary" />
      <b class="font-semibold">{{ report.title }}</b>
      <span class="text-meta text-muted-foreground tabular-nums">{{ report.cost.time }}</span>
      <Button type="button" variant="quiet" size="sm" class="ml-auto" data-action="report-download" @click="downloadReport">
        <Download />내려받기
      </Button>
    </div>
    <dl class="m-0 grid grid-cols-[34px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 px-3 py-2.5 text-ui">
      <dt class="text-muted-foreground">종합</dt>
      <dd class="m-0 flex flex-wrap items-center gap-x-1.5">
        <s :class="GRADES[report.overall_grade[0]].textClass" class="opacity-70">{{ report.overall[0] }}</s>
        <ArrowRight class="size-3 text-subtle-foreground" />
        <b class="font-semibold" :class="GRADES[report.overall_grade[1]].textClass">{{ report.overall[1] }}</b>
        <span class="text-meta text-muted-foreground tabular-nums">
          · 심각 <s>{{ report.counts[0].bad }}</s> {{ report.counts[1].bad }} · 주의 <s>{{ report.counts[0].warn }}</s> {{ report.counts[1].warn }}
        </span>
      </dd>
      <template v-if="report.kpi.length">
        <dt class="text-muted-foreground">점수</dt>
        <dd class="m-0 flex flex-wrap gap-x-2 tabular-nums">
          <span v-for="item in report.kpi" :key="item.name">{{ item.name }} <s class="text-subtle-foreground">{{ item.from }}</s> {{ item.to }}</span>
        </dd>
      </template>
      <template v-if="report.rows.length">
        <dt class="text-muted-foreground">고침</dt>
        <dd class="m-0">
          <div v-for="row in report.rows" :key="row.name" class="grid grid-cols-[minmax(0,1fr)_auto_14px_auto_16px] items-center gap-1.5">
            <span class="truncate">{{ row.name }}</span>
            <s class="text-right text-subtle-foreground tabular-nums">{{ row.from }}</s>
            <ArrowRight class="size-3 text-subtle-foreground" />
            <b class="text-right font-semibold tabular-nums" :class="GRADES[row.grade].textClass">{{ row.to }}</b>
            <component :is="GRADES[row.grade].icon" class="size-3.5" :class="GRADES[row.grade].iconClass" :stroke-width="2.2" />
          </div>
          <span v-if="report.more" class="text-meta text-muted-foreground">+ 고친 검사 {{ report.more }}</span>
        </dd>
      </template>
      <dt class="text-muted-foreground">바꿈</dt>
      <dd class="m-0">{{ report.changed || '—' }}</dd>
      <dt class="text-muted-foreground">비용</dt>
      <dd class="m-0 tabular-nums">LLM {{ fmt(report.cost.llm) }} · Jev {{ fmt(report.cost.jev) }}</dd>
    </dl>
  </div>

  <!-- 알림 -->
  <div v-else-if="event.kind === 'notice'" class="my-2 ml-6 text-ui" data-event="notice">
    <div class="flex items-center gap-1.5" :class="NOTICE_TONE[text('tone') || 'info']">
      <TriangleAlert v-if="text('tone') === 'bad' || text('tone') === 'warn'" class="size-3.5" />
      <Info v-else class="size-3.5" />
      <span :class="openChecks.length ? 'font-semibold' : ''">{{ text('text') }}</span>
    </div>
    <div v-if="openChecks.length" class="mt-1 ml-5 grid gap-1" data-notice-checks>
      <span v-for="check in openChecks" :key="check.name" class="flex items-center gap-1.5">
        <component :is="GRADES[check.grade].icon" class="size-3.5" :class="GRADES[check.grade].iconClass" :stroke-width="2.2" />
        <span class="font-[550]">{{ check.name }}</span>
        <b class="font-semibold" :class="GRADES[check.grade].textClass">{{ check.value }}</b>
      </span>
    </div>
  </div>
</template>
