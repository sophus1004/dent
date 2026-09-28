// 켜 둘 모듈 목록. 모듈을 더하거나 빼려면 import 한 줄과 목록의 한 칸을 고친다.
import classification from '@/modules/classification'
import retrieval from '@/modules/retrieval'
import type { DentModule } from '@/system/module'

export const modules: DentModule[] = [classification, retrieval]
