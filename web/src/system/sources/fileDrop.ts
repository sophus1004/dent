// 파일을 놓을 칸 밖에 파일을 끌어 놓았을 때 브라우저가 그 파일을 열어 DENT 화면을 떠나지 않게 막는다.
// 앱을 켤 때 App.vue가 한 번 부른다. 파일을 받는 칸(SourcePicker)은 자기가 먼저 preventDefault를 하므로
// 여기서는 아직 아무도 받지 않은 파일 끌기만 막고, 커서를 '놓을 수 없음'으로 보인다.
// 글자를 입력칸에 끌어 놓는 것처럼 파일이 아닌 끌기는 건드리지 않는다.

function hasFiles(event: DragEvent): boolean {
  return event.dataTransfer?.types.includes('Files') ?? false
}

function blockDragOver(event: DragEvent): void {
  const isUnclaimedFileDrag = hasFiles(event) && !event.defaultPrevented
  if (!isUnclaimedFileDrag) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
}

function blockDrop(event: DragEvent): void {
  if (hasFiles(event)) event.preventDefault()
}

let isInstalled = false

/** 창 전체에 파일 끌어 놓기 막기를 건다. 여러 번 불러도 한 번만 건다. */
export function preventFileDropOutsideZones(): void {
  if (isInstalled) return
  window.addEventListener('dragover', blockDragOver)
  window.addEventListener('drop', blockDrop)
  isInstalled = true
}
