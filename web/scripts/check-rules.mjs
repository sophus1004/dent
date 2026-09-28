// 화면 코드의 층 규칙을 검사한다. 빌드 전에 돈다(pnpm build).
// 1. system(시스템 층)은 modules를 import하지 않는다.
// 2. 한 모듈은 다른 모듈을 import하지 않는다.
// 3. modules를 import하는 곳은 src/main.ts와 src/modules.ts뿐이다.
// 4. 모듈 목록(src/modules.ts)은 src/main.ts만 import한다. 목록은 모든 모듈을 불러오기 때문이다.
// 5. v-html을 쓰지 않는다. 데이터 속 글자가 HTML로 실행되지 않게 하려는 것이다.
// 6. 모든 파일은 이 파일이 하는 일을 적은 한국어 주석(// 또는 <!-- -->)으로 시작한다.
//    shadcn-vue로 새 부품(src/system/ui)을 더하면 맨 위에 한 줄을 달아야 통과한다.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(WEB, 'src')
const MODULES_DIR = 'src/modules/'
const SYSTEM_DIR = 'src/system/'
const ENTRY_FILES = ['src/main.ts', 'src/modules.ts']
// 모듈 목록 파일. import 경로에는 확장자가 없을 수도 있다('@/modules').
const MODULE_LIST = ['src/modules', 'src/modules.ts']
const MODULE_LIST_USER = 'src/main.ts'
// import x from 'a' · export { x } from 'a' · import 'a' · import('a')
const FROM_PATTERN = /(?:import|export)[^'"`]*?from\s*['"]([^'"]+)['"]/.source
const BARE_PATTERN = /import\s*\(?\s*['"]([^'"]+)['"]/.source
const IMPORT_PATTERN = new RegExp(`${FROM_PATTERN}|${BARE_PATTERN}`, 'g')
// 맨 위 주석에 한글이 한 글자라도 있는지 본다.
const HANGUL = /[가-힣]/

function listFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return listFiles(path)
    return /\.(ts|vue)$/.test(name) ? [path] : []
  })
}

// import 경로를 web/ 기준 경로로 바꾼다. 바깥 패키지(vue 등)는 null.
function resolveImport(file, spec) {
  if (spec.startsWith('@/')) return 'src/' + spec.slice(2)
  if (spec.startsWith('.')) return relative(WEB, resolve(dirname(file), spec))
  return null
}

// 파일 맨 위의 주석. 주석으로 시작하지 않으면 ''
function topComment(code) {
  if (code.startsWith('<!--')) return code.slice(0, code.indexOf('-->'))
  if (code.startsWith('/*')) return code.slice(0, code.indexOf('*/'))
  if (code.startsWith('//')) return code.split('\n', 1)[0]
  return ''
}

// 'src/modules/classification/api' → 'classification'. 모듈 밖이면 null
const moduleOf = (path) =>
  path.startsWith(MODULES_DIR) ? path.slice(MODULES_DIR.length).split('/')[0] : null
const problems = []

for (const file of listFiles(SRC)) {
  const self = relative(WEB, file)
  const code = readFileSync(file, 'utf8')
  if (code.includes('v-html')) problems.push(`${self}: v-html을 쓰지 않습니다. 글자는 {{ }}로 넣으세요.`)
  const hasTopComment = HANGUL.test(topComment(code))
  if (!hasTopComment) problems.push(`${self}: 맨 위에 이 파일이 하는 일을 한국어 주석으로 적으세요.`)
  for (const match of code.matchAll(IMPORT_PATTERN)) {
    const target = resolveImport(file, match[1] ?? match[2])
    const isModuleList = MODULE_LIST.includes(target)
    if (isModuleList && self !== MODULE_LIST_USER) {
      problems.push(`${self}: 모듈 목록(src/modules.ts)은 ${MODULE_LIST_USER}만 import합니다.`)
      continue
    }
    const targetModule = target && moduleOf(target)
    if (!targetModule) continue
    const selfModule = moduleOf(self)
    const isSystem = self.startsWith(SYSTEM_DIR)
    const isOtherModule = selfModule !== null && selfModule !== targetModule
    const isStray = selfModule === null && !ENTRY_FILES.includes(self)
    if (isSystem) problems.push(`${self}: system은 모듈(${target})을 import하지 않습니다.`)
    else if (isOtherModule) problems.push(`${self}: 다른 모듈(${target})을 import하지 않습니다.`)
    else if (isStray) problems.push(`${self}: 모듈은 src/main.ts와 src/modules.ts에서만 import합니다.`)
  }
}

if (problems.length) {
  console.error(`층 규칙 위반 ${problems.length}건\n` + problems.map((p) => '  - ' + p).join('\n'))
  process.exit(1)
}
console.log('층 규칙 검사 통과')
