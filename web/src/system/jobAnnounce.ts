// 끝난 작업을 알림으로 바꾸고, 그 작업을 낸 모듈에도 알린다(jobFeed가 새로 끝난 작업마다 부른다).
//   실패   닫을 때까지 · [보기](작업 기록에서 그 작업) · [다시 하기](모듈이 알려 준 종류일 때)
//   완료   5초 · 시스템 정리 작업은 알리지 않는다
//   멈춤   알리지 않는다(사용자가 직접 멈췄다)
import { useRouter } from 'vue-router'

import { durationText } from '@/system/format'
import { errorMessage } from '@/system/http'
import { canRetry, findJobKind, jobKindName, progressText } from '@/system/jobKinds'
import { useModules, type ModuleJobKind } from '@/system/module'
import { pushToast, type ToastAction } from '@/system/toasts'
import type { JobRead } from '@/system/types'

// 알리지 않는 작업의 모듈 (시스템 정리)
const QUIET_MODULE = 'system'

/** 끝난 작업을 알림으로 띄우는 함수와 다시 하기 함수. 앱 틀(App.vue)이 한 번 만든다. */
export function useJobAnnouncer(): { announce: (job: JobRead) => void; retry: (job: JobRead) => Promise<void> } {
  const modules = useModules()
  const router = useRouter()

  function open(job: JobRead): void {
    void router.push({ name: 'jobs', query: { job: String(job.id) } })
  }

  /** 실패 · 멈춘 작업을 다시 넣고 알린다. 다시 넣지 못하면 그 까닭을 알린다. */
  async function retry(job: JobRead): Promise<void> {
    const kind: ModuleJobKind | null = findJobKind(modules, job)
    const name = jobKindName(modules, job)
    try {
      await kind?.retry?.(job)
      pushToast({ tone: 'done', title: `다시 넣음 · ${name}`, detail: job.dataset_name ?? '', actions: [] })
    } catch (error) {
      pushToast({ tone: 'fail', title: `다시 하기 실패 · ${name}`, detail: errorMessage(error), actions: [] })
    }
  }

  function announce(job: JobRead): void {
    // 작업을 낸 모듈이 자기 목록을 맞출 수 있게 먼저 알린다.
    modules.find((module) => module.id === job.module)?.onJobFinished?.(job)
    const kind = findJobKind(modules, job)
    const name = jobKindName(modules, job)
    if (job.status === 'failed') {
      const actions: ToastAction[] = [{ label: '보기', run: () => open(job) }]
      if (canRetry(kind, job)) actions.push({ label: '다시 하기', run: () => retry(job), primary: true })
      const detail = [job.dataset_name, progressText(kind, job), job.error].filter(Boolean).join(' · ')
      pushToast({ tone: 'fail', title: `실패 · ${name}`, detail, actions })
      return
    }
    const isQuiet = job.status !== 'done' || job.module === QUIET_MODULE
    if (isQuiet) return
    const detail = [job.dataset_name, durationText(job.started_at, job.finished_at)].filter(Boolean).join(' · ')
    pushToast({ tone: 'done', title: `완료 · ${name}`, detail, actions: [] })
  }

  return { announce, retry }
}
