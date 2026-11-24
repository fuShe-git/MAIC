/** 下发到课件 / 视频的播放设置 */
export interface CourseSettings {
  autoPlay: boolean
  rate: number
  volume: number
}

interface CourseApi {
  setAutoPlay?: (enabled: boolean) => void
  setRate?: (rate: number) => void
  setVolume?: (volume: number) => void
}

type CourseWindow = Window & { __coursePlayer?: CourseApi }

const VOLUME_KEY = 'maic.course.volume'
const endedGuards = new WeakMap<Document, (event: Event) => void>()
let warnedOnce = false

/** 音量限制在 0~1，非法值回退为 1 */
export function clampVolume(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1
}

/** 读取本地保存的音量，隐私模式或数据损坏时回退为 1 */
export function readStoredVolume(): number {
  try {
    const raw = window.localStorage.getItem(VOLUME_KEY)
    if (raw === null) return 1
    return clampVolume(Number(raw))
  } catch (error) {
    console.error('读取本地音量设置失败', error)
    return 1
  }
}

/** 音量持久化，只保存数字 */
export function storeVolume(volume: number): void {
  try {
    window.localStorage.setItem(VOLUME_KEY, String(clampVolume(volume)))
  } catch (error) {
    console.error('保存本地音量设置失败', error)
  }
}

function getCourseApi(frame: HTMLIFrameElement | null): CourseApi | null {
  const api = (frame?.contentWindow as CourseWindow | undefined)?.__coursePlayer
  return api && typeof api === 'object' ? api : null
}

/** 旧课件没有控制接口：在 document 捕获阶段拦住 ended，避免内部自动翻页 */
function setLegacyAutoPlayGuard(doc: Document, enabled: boolean): void {
  const existing = endedGuards.get(doc)
  if (existing) {
    doc.removeEventListener('ended', existing, true)
    endedGuards.delete(doc)
  }
  if (enabled) return
  const guard = (event: Event) => event.stopPropagation()
  doc.addEventListener('ended', guard, true)
  endedGuards.set(doc, guard)
}

/**
 * 向同源课件 iframe 下发设置。
 * 返回 true 表示使用了课件暴露的控制接口，false 表示走了旧课件兼容模式。
 */
export function applyCourseSettings(
  frame: HTMLIFrameElement | null,
  settings: CourseSettings,
): boolean {
  const doc = frame?.contentDocument
  if (!doc) return false

  const api = getCourseApi(frame)
  if (api) {
    api.setAutoPlay?.(settings.autoPlay)
    api.setRate?.(settings.rate)
    api.setVolume?.(settings.volume)
    return true
  }

  if (!warnedOnce) {
    console.warn('当前课件未提供播放控制接口，已启用兼容模式')
    warnedOnce = true
  }

  const audio = doc.getElementById('player') as HTMLAudioElement | null
  if (audio) {
    audio.playbackRate = settings.rate
    audio.volume = clampVolume(settings.volume)
    setLegacyAutoPlayGuard(doc, settings.autoPlay)
  }
  return false
}

/** 视频兜底源的倍速与音量 */
export function applyVideoSettings(
  video: HTMLVideoElement | null,
  settings: CourseSettings,
): void {
  if (!video) return
  video.playbackRate = settings.rate
  video.volume = clampVolume(settings.volume)
}
