<!--
  연결 설정의 한 줄 (임베딩 · Jev · LLM): 이름 · 저장 태그 · 상태(점 + '연결됨 · 1024차원 · 42ms' / '실패 · 연결 거부'),
  주소 · 모델 입력칸, [연결 확인] [저장] [연결 끊기].
  [연결 확인]은 저장하지 않고 지금 입력만 확인한다. [저장]은 저장하고 바로 확인한다(다시 실행할 필요 없음).
  입력을 바꾸면 전의 결과는 지금 입력의 것이 아니므로 '확인 전'으로 보인다.
  llm이면 공급자(OpenAI · Anthropic · Google · xAI · vLLM)와 API 키 칸이 더 있다.
    공급자를 바꾸면 주소가 비었거나 앞 공급자의 주소일 때 새 공급자의 주소로 바꿔 넣는다.
    API 키는 가려서 받고, 저장된 키는 앞뒤 몇 글자(힌트)만 빈 글자로 보인다. 비워 두면 저장된 키를 쓴다.
    모델은 비워 두고 [연결 확인]을 누르면 모델 목록을 받아 고를 수 있다(저장할 때는 모델이 있어야 한다).
  실행할 때 내장 모델을 올린 역할(initial.embedded)은 잠긴다: '내장' 태그, 입력칸을 막고 [저장] · [연결 끊기] 대신
  '실행할 때 고름'을 보인다. [연결 확인]은 된다. 내장 모델이 준비되기 전이면 상태가 '내려받는 중' · '확인하는 중' · '불러오는 중'이다.
-->
<script setup lang="ts">
import { LoaderCircle, Lock, Plug, Unplug, type LucideIcon } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { checkConnection, deleteConnection, saveConnection } from '@/system/api'
import { checkLine, isAddress, LLM_PROVIDERS, providerAddress } from '@/system/connections/connections'
import { errorMessage } from '@/system/http'
import SourceTag from '@/system/pages/SourceTag.vue'
import type { ConnectionCheckRead, ConnectionRead, ConnectionRole, ConnectionUpdate, LlmProvider } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Input } from '@/system/ui/input'
import { NativeSelect } from '@/system/ui/native-select'

const props = defineProps<{
  role: ConnectionRole
  icon: LucideIcon
  // 예: '임베딩'
  name: string
  // 주소 칸의 빈 글자. 예: 'http://127.0.0.1:8010'
  addressPlaceholder: string
  // 모델 칸의 빈 글자. 예: '자동', '필수'
  modelPlaceholder: string
  // 모델 이름이 꼭 있어야 하는지 (임베딩)
  modelRequired: boolean
  // 저장된 연결 (GET /connections). 읽는 중이면 null
  initial: ConnectionRead | null
  // 저장된 연결을 읽는 중인지. 읽는 동안은 입력을 막는다.
  loading: boolean
  // LLM이면 공급자 · API 키 칸을 둔다
  llm?: boolean
}>()

const emit = defineEmits<{
  // 저장하거나 끊었다. 상태(/readyz)를 다시 물어보게 한다.
  changed: []
}>()

// 연결 값 한 벌 (결과가 지금 입력의 것인지 견줄 때 쓴다). key는 새로 친 키(비었으면 저장된 키를 쓴다).
interface Target {
  address: string
  model: string
  provider: LlmProvider | null
  key: string
}

// 확인 결과와 그때의 입력
interface ShownResult {
  check: ConnectionCheckRead
  target: Target
}

// LLM 공급자를 처음 고를 때의 값
const DEFAULT_PROVIDER: LlmProvider = 'openai'

const address = ref('')
const model = ref('')
const provider = ref<LlmProvider | null>(null)
// 새로 치는 API 키. 저장된 키는 화면에 오지 않는다(hint만).
const apiKey = ref('')
const keyHint = ref<string | null>(null)
// 저장된 값. 저장하지 않았으면 null
const saved = ref<Target | null>(null)
const result = ref<ShownResult | null>(null)
// 지금 하는 일. 하는 동안 버튼을 막는다.
const busy = ref<'check' | 'save' | 'delete' | null>(null)
const failure = ref<string | null>(null)

watch(
  () => props.initial,
  (connection) => {
    const savedAddress = connection?.base_url ?? null
    provider.value = props.llm ? (connection?.provider ?? DEFAULT_PROVIDER) : null
    address.value = savedAddress ?? (props.llm ? providerAddress(provider.value) : '')
    model.value = connection?.model ?? ''
    apiKey.value = ''
    keyHint.value = connection?.api_key_hint ?? null
    saved.value = savedAddress ? currentTarget(savedAddress) : null
    result.value =
      saved.value && connection?.check ? { check: connection.check, target: saved.value } : null
    failure.value = null
  },
  { immediate: true },
)

// 공급자를 바꾸면: 주소가 비었거나 앞 공급자의 주소였으면 새 공급자의 주소로
watch(provider, (next, previous) => {
  if (!props.llm || previous === undefined) return
  const wasDefault = cleanAddress.value === '' || cleanAddress.value === providerAddress(previous)
  if (wasDefault) address.value = providerAddress(next)
})

// 서버가 저장할 모양: 앞뒤 공백과 끝의 /를 뗀다
const cleanAddress = computed(() => address.value.trim().replace(/\/+$/, ''))
const cleanModel = computed(() => model.value.trim())
const cleanKey = computed(() => apiKey.value.trim())

const isAddressWrong = computed(() => cleanAddress.value !== '' && !isAddress(cleanAddress.value))
// 확인은 주소만 있으면 된다(LLM은 모델을 비우면 목록을 받는다). 저장은 모델도 있어야 한다(임베딩 · LLM).
const canCheck = computed(
  () => isAddress(cleanAddress.value) && (!props.modelRequired || cleanModel.value !== ''),
)
const canSave = computed(() => canCheck.value && (!props.llm || cleanModel.value !== ''))
const isSameAsSaved = computed(() => sameTarget(saved.value, currentTarget(cleanAddress.value)))
// 보이는 결과가 지금 입력의 것인지
const isResultCurrent = computed(() => sameTarget(result.value?.target ?? null, currentTarget(cleanAddress.value)))

// 마지막 확인이 알려 준 모델 목록 (LLM). 모델 칸의 고를 것으로 쓴다.
const modelOptions = computed(() => {
  const models = result.value?.check.facts.models
  return Array.isArray(models) ? models : []
})

// 키 칸의 빈 글자: 저장된 키가 있으면 힌트, vLLM은 없어도 됨
const keyPlaceholder = computed(() => {
  if (keyHint.value) return `저장됨 · ${keyHint.value}`
  return provider.value === 'vllm' ? '없어도 됨' : '필수'
})

// 실행할 때 내장 모델을 올린 역할이면 잠긴다(바꾸려면 다시 실행하며 고른다)
const isLocked = computed(() => Boolean(props.initial?.embedded))

// 이름 옆 태그: 저장됨 · 저장 전 (내장이면 '내장' 태그를 따로 보인다)
const savedTag = computed(() => {
  if (isLocked.value) return ''
  if (saved.value && isSameAsSaved.value) return '저장됨'
  if (cleanAddress.value !== '') return '저장 전'
  return ''
})

// 오른쪽 상태 줄
const status = computed(() => {
  if (busy.value === 'check' || busy.value === 'save') return { tone: 'busy', word: '확인 중', rest: '' }
  if (failure.value) return { tone: 'bad', word: '오류', rest: failure.value }
  if (isAddressWrong.value) return { tone: 'bad', word: '주소', rest: 'http:// · https://' }
  if (result.value && isResultCurrent.value) {
    const line = checkLine(result.value.check)
    const isPreparing = line.word === '내려받는 중' || line.word === '확인하는 중' || line.word === '불러오는 중'
    const tone = result.value.check.ok ? 'good' : isPreparing ? 'run' : 'bad'
    return { tone, word: line.word, rest: line.rest }
  }
  if (!saved.value && cleanAddress.value === '') return { tone: 'off', word: '미연결', rest: '' }
  return { tone: 'off', word: '확인 전', rest: '' }
})

// 상태 낱말의 글자색과 점
const WORD_CLASS: Record<string, string> = {
  good: 'text-success-ink',
  bad: 'text-danger-ink',
  run: 'text-accent-foreground',
  off: 'text-muted-foreground',
  busy: 'text-muted-foreground',
}
const DOT_CLASS: Record<string, string> = {
  good: 'bg-success',
  bad: 'bg-danger',
  run: 'bg-primary',
  off: 'ring-[1.5px] ring-subtle-foreground ring-inset',
}

// 입력칸 이름의 폭: LLM은 'API 키'가 들어가게 넓게
const labelWidth = computed(() => (props.llm ? 'grid-cols-[52px_minmax(0,1fr)]' : 'grid-cols-[36px_minmax(0,1fr)]'))
const buttonIndent = computed(() => (props.llm ? 'pl-16' : 'pl-12'))

function currentTarget(forAddress: string): Target {
  return { address: forAddress, model: cleanModel.value, provider: provider.value, key: cleanKey.value }
}

function sameTarget(a: Target | null, b: Target | null): boolean {
  if (!a || !b) return false
  return a.address === b.address && a.model === b.model && a.provider === b.provider && a.key === b.key
}

function body(): ConnectionUpdate {
  const data: ConnectionUpdate = { base_url: cleanAddress.value, model: cleanModel.value || null }
  if (props.llm) {
    data.provider = provider.value
    data.api_key = cleanKey.value || null
  }
  return data
}

/** 저장하지 않고 지금 입력으로 연결을 확인한다. */
async function runCheck(): Promise<void> {
  if (!canCheck.value || busy.value) return
  busy.value = 'check'
  failure.value = null
  const tried = currentTarget(cleanAddress.value)
  try {
    const check = await checkConnection(props.role, body())
    result.value = { check, target: tried }
  } catch (error) {
    failure.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

/** 저장하고 바로 확인한다. 저장한 키는 입력칸에서 지우고 힌트로만 보인다. */
async function runSave(): Promise<void> {
  if (!canSave.value || busy.value) return
  busy.value = 'save'
  failure.value = null
  try {
    const connection = await saveConnection(props.role, body())
    address.value = connection.base_url ?? cleanAddress.value
    model.value = connection.model ?? ''
    apiKey.value = ''
    keyHint.value = connection.api_key_hint
    saved.value = currentTarget(address.value)
    result.value = connection.check ? { check: connection.check, target: saved.value } : null
    emit('changed')
  } catch (error) {
    failure.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

/** 저장한 연결을 지운다(저장한 키도 함께). 입력칸의 값은 남겨 두어 다시 저장할 수 있다. */
async function runDelete(): Promise<void> {
  if (!saved.value || busy.value) return
  busy.value = 'delete'
  failure.value = null
  try {
    await deleteConnection(props.role)
    saved.value = null
    result.value = null
    keyHint.value = null
    emit('changed')
  } catch (error) {
    failure.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <form class="px-5 py-4" :aria-label="name" :data-connection="role" @submit.prevent="runCheck">
    <div class="flex min-w-0 items-center gap-2">
      <component :is="icon" class="size-4 shrink-0 text-muted-foreground" />
      <span class="text-body font-semibold whitespace-nowrap">{{ name }}</span>
      <SourceTag v-if="initial?.embedded" :label="`내장 · ${initial.embedded}`" embedded />
      <span
        v-if="savedTag"
        class="inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]"
      >
        {{ savedTag }}
      </span>
      <span
        class="ml-auto inline-flex min-w-0 items-center gap-1.5 text-ui"
        :title="status.rest ? `${status.word} · ${status.rest}` : status.word"
        data-status
      >
        <LoaderCircle v-if="status.tone === 'busy'" class="size-3.5 shrink-0 animate-spin text-muted-foreground" />
        <span v-else class="size-1.5 shrink-0 rounded-full" :class="DOT_CLASS[status.tone]" />
        <b class="shrink-0 font-semibold" :class="WORD_CLASS[status.tone]">{{ status.word }}</b>
        <span v-if="status.rest" class="truncate text-muted-foreground">· {{ status.rest }}</span>
      </span>
    </div>

    <div class="mt-3 grid items-center gap-x-3 gap-y-2" :class="labelWidth">
      <template v-if="llm">
        <label :for="`connection-${role}-provider`" class="text-ui text-muted-foreground">공급자</label>
        <NativeSelect :id="`connection-${role}-provider`" v-model="provider" :disabled="loading || isLocked" data-field="provider">
          <option v-for="item in LLM_PROVIDERS" :key="item.value" :value="item.value">{{ item.name }}</option>
        </NativeSelect>
      </template>
      <label :for="`connection-${role}-address`" class="text-ui text-muted-foreground">주소</label>
      <Input
        :id="`connection-${role}-address`"
        v-model="address"
        :placeholder="addressPlaceholder"
        :disabled="loading || isLocked"
        :aria-invalid="isAddressWrong || undefined"
        class="font-mono text-meta"
        autocomplete="off"
        spellcheck="false"
      />
      <template v-if="llm">
        <label :for="`connection-${role}-key`" class="text-ui text-muted-foreground">API 키</label>
        <Input
          :id="`connection-${role}-key`"
          v-model="apiKey"
          type="password"
          :placeholder="keyPlaceholder"
          :disabled="loading"
          class="font-mono text-meta"
          autocomplete="off"
          spellcheck="false"
          data-field="api-key"
        />
      </template>
      <label :for="`connection-${role}-model`" class="text-ui text-muted-foreground">모델</label>
      <Input
        :id="`connection-${role}-model`"
        v-model="model"
        :placeholder="modelPlaceholder"
        :disabled="loading || isLocked"
        :list="llm && modelOptions.length ? `connection-${role}-models` : undefined"
        class="font-mono text-meta"
        autocomplete="off"
        spellcheck="false"
      />
      <datalist v-if="llm" :id="`connection-${role}-models`">
        <option v-for="name in modelOptions" :key="name" :value="name" />
      </datalist>
    </div>

    <div class="mt-3 flex items-center gap-2" :class="buttonIndent">
      <Button type="submit" variant="outline" size="sm" :disabled="!canCheck || busy !== null" data-action="check">
        <Plug />연결 확인
      </Button>
      <span v-if="isLocked" class="ml-auto inline-flex items-center gap-1.5 text-meta text-muted-foreground" data-locked>
        <Lock class="size-3.5" />실행할 때 고름
      </span>
      <template v-else>
        <Button type="button" size="sm" :disabled="!canSave || busy !== null" data-action="save" @click="runSave">
          저장
        </Button>
        <span v-if="llm && modelOptions.length" class="text-meta text-muted-foreground">
          모델 {{ modelOptions.length }}개
        </span>
        <Button
          type="button"
          variant="quiet"
          size="sm"
          class="ml-auto hover:text-danger-ink"
          :disabled="!saved || busy !== null"
          data-action="delete"
          @click="runDelete"
        >
          <Unplug />연결 끊기
        </Button>
      </template>
    </div>
  </form>
</template>
