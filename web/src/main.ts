// 앱을 켠다: 글꼴과 공통 모양을 불러오고, 테마를 적용하고, 모듈 목록으로 라우터를 만들어 화면에 붙인다.
// 모듈을 import해도 되는 곳은 이 파일과 modules.ts뿐이다(scripts/check-rules.mjs가 검사).
// 글꼴(Pretendard)은 CDN 없이 npm 패키지에서 가져와 함께 묶는다. 쓰는 글자 묶음만 내려받는다.
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import '@/system/globals.css'

import { createApp } from 'vue'

// src/modules.ts (켜 둘 모듈 목록)
import { modules } from '@/modules'
import App from '@/system/App.vue'
import { provideModules } from '@/system/module'
import { createAppRouter } from '@/system/router'
import { applyTheme } from '@/system/theme'

applyTheme()
const app = createApp(App)
provideModules(app, modules)
app.use(createAppRouter(modules))
app.mount('#app')
