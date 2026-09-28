// 명령 목록 부품들과, 그 부품들이 거르기 상태를 나눠 갖는 context를 내보낸다 (shadcn-vue).
import type { Ref } from "vue"
import { createContext } from "reka-ui"

export { default as Command } from "./Command.vue"
export { default as CommandDialog } from "./CommandDialog.vue"
export { default as CommandEmpty } from "./CommandEmpty.vue"
export { default as CommandGroup } from "./CommandGroup.vue"
export { default as CommandInput } from "./CommandInput.vue"
export { default as CommandItem } from "./CommandItem.vue"
export { default as CommandList } from "./CommandList.vue"

export const [useCommand, provideCommandContext] = createContext<{
  allItems: Ref<Map<string, string>>
  allGroups: Ref<Map<string, Set<string>>>
  filterState: {
    search: string
    filtered: { count: number, items: Map<string, number>, groups: Set<string> }
  }
}>("Command")

export const [useCommandGroup, provideCommandGroupContext] = createContext<{
  id?: string
}>("CommandGroup")
