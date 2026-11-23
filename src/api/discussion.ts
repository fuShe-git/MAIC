import { API_BASE, fetchSse } from './http'
import type { SseSignal } from './http'

/** 回答来源：课件 / 网络 / 两者混合 */
export type DiscussionSource = 'courseware' | 'web' | 'mixed'

/** 单条引用来源 */
export interface DiscussionSourceItem {
  kind: 'courseware' | 'web'
  label: string
  url?: string
  snippet?: string
}

/** 单条讨论消息（仅存在前端本次打开期间） */
export interface DiscussionMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  source?: DiscussionSource
  sources?: DiscussionSourceItem[]
  error?: boolean
}

/** 首次提问携带的课件单页正文 */
export interface DiscussionPage {
  ind: number
  context: string
}

/** 讨论请求体 */
export interface DiscussionPayload {
  message: string
  courseId: string
  topic: string
  pageNum: string
  firstQuestion: boolean
  coursePages?: DiscussionPage[]
  history: Array<{ role: 'user' | 'assistant'; content: string }>
}

/** 讨论 SSE 事件，字段与后端扁平事件保持一致 */
export type DiscussionEvent =
  | {
      type: 'meta'
      source?: DiscussionSource
      searching?: boolean
      compressed?: boolean
      notice?: string
    }
  | { type: 'delta'; text: string }
  | { type: 'sources'; items: DiscussionSourceItem[] }
  | { type: 'done'; source?: DiscussionSource; compressed?: boolean }
  | { type: 'error'; message: string }

/**
 * 流式讨论：复用 http.ts 的 SSE 解析器，不另写 fetch 流处理。
 */
export function discussionStream(
  payload: DiscussionPayload,
  onEvent: (event: DiscussionEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return fetchSse<DiscussionEvent>(`${API_BASE}/discussion/stream`, onEvent, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}
