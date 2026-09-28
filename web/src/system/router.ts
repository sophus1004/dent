// 화면 주소. 주소는 # 뒤에 둔다(#/classification/new). 그래서 서버는 index.html 하나만 내주면 된다.
// system은 모듈을 모르므로, main.ts가 모듈 목록을 넘겨 주면 그 주소들을 끼워 넣는다.
import { createRouter, createWebHashHistory, type Router, type RouteRecordRaw } from 'vue-router'

import type { DentModule } from '@/system/module'
import HomePage from '@/system/pages/HomePage.vue'
import JobsPage from '@/system/pages/JobsPage.vue'
import NotFoundPage from '@/system/pages/NotFoundPage.vue'
import StatusPage from '@/system/pages/StatusPage.vue'

/** 공통 화면(홈, 상태, 작업 기록, 없는 화면)과 모듈 화면을 합친 라우터를 만든다. */
export function createAppRouter(modules: DentModule[]): Router {
  const routes: RouteRecordRaw[] = [
    { path: '/', name: 'home', component: HomePage },
    { path: '/status', name: 'status', component: StatusPage },
    { path: '/jobs', name: 'jobs', component: JobsPage },
    ...modules.flatMap((module) => module.routes),
    // 위에서 못 찾은 주소는 모두 여기로 온다. 반드시 맨 뒤에 둔다.
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundPage },
  ]
  return createRouter({
    history: createWebHashHistory(),
    routes,
    // 다른 화면으로 가면 맨 위부터 보여 준다. 같은 화면에서 주소 뒤 ?만 바뀌면(거르기, 문장 열기) 그대로 둔다.
    scrollBehavior: (to, from, savedPosition) => {
      if (savedPosition) return savedPosition
      const isSameScreen = to.path === from.path
      return isSameScreen ? false : { top: 0 }
    },
  })
}
