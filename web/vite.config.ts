// 화면 빌드 설정.
// 개발 중(pnpm dev)에는 5173 포트로 화면을 열고, API 요청은 8000 포트의 DENT로 넘긴다.
// 빌드 결과(dist/)는 FastAPI가 / 에서 내주므로 파일 주소를 상대 경로(./)로 만든다.
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// DENT API 서버 주소. 런처가 켜는 기본 주소와 같다.
const API_SERVER = 'http://127.0.0.1:8000'

export default defineConfig({
  base: './',
  plugins: [vue(), tailwindcss()],
  resolve: {
    // '@/system/http'처럼 src 아래를 절대 경로로 쓴다.
    alias: { '@': '/src' },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 5173이 이미 쓰이면 다른 포트로 몰래 옮기지 않고 멈춘다.
    strictPort: true,
    proxy: {
      '/api': API_SERVER,
      '/healthz': API_SERVER,
      '/readyz': API_SERVER,
    },
  },
})
