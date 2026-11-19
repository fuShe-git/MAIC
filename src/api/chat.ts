/** 大纲里的一个模块，与后端 CatalogItem 对应 */
export interface CatalogItem {
  /** 模块序号，从 1 开始 */
  ind: number
  /** 该模块内各知识点的标题 */
  title: string[]
  /** 与 title 一一对应的口播讲稿 */
  context: string[]
}

/** 一个模块对应的页面，即「本部分大纲 + 渲染用 HTML」 */
export interface PageItem {
  /** 模块序号，与 catalogList 中同序号模块的 ind 一致 */
  ind: number
  /** 本部分的大纲文本，即标题 + 口播讲稿 */
  context: string
  /** 本部分对应的页面，完整 HTML 文档 */
  htmlpage: string
}

/** 后端 /api/chat 的返回结构（字段与 server/app/api/routes/chat.py 的 ChatResponse 对齐） */
export interface ChatResponse {
  /** 大模型回复的文本；闲聊时仅此字段有值 */
  reply: string
  /** 是否需要讲解；false 时下面的课件字段都是空的 */
  needLecture: boolean
  /** 识别出的讲解主题 */
  topic: string
  /** 讲解大纲，已按由浅入深的顺序排好 */
  catalogList: CatalogItem[]
  /** 讲解页面，与 catalogList 按 ind 一一对应 */
  pageList: PageItem[]
  /** 组装后的自包含课件 HTML 落盘路径（Windows 绝对路径）；未生成时为空串 */
  courseHtml: string
  /** 导出的 MP4 落盘路径（Windows 绝对路径）；未生成或导出失败时为空串 */
  videoUrl: string
}

/** 后端缺省字段时的兜底值，保证前端拿到的一定是完整结构 */
const EMPTY_RESPONSE: ChatResponse = {
  reply: '',
  needLecture: false,
  topic: '',
  catalogList: [],
  pageList: [],
  courseHtml: '',
  videoUrl: '',
}

/**
 * 调用后端对话接口，返回完整响应对象。
 * 开发环境由 Vite 代理转发到 http://127.0.0.1:8001/api/chat
 */
export async function sendMessage(
  message: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })

  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const data: { detail?: string } = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      // 响应体不是 JSON 时忽略，使用默认提示
    }
    throw new Error(detail)
  }

  const data = (await response.json()) as Partial<ChatResponse>
  return { ...EMPTY_RESPONSE, ...data }
}

/** 生成进度事件的步骤名，与后端 nodes.py 的 _emit step 对齐 */
export type ProgressStep =
  | 'check'
  | 'topic'
  | 'outline'
  | 'pages'
  | 'audio'
  | 'assemble'
  | 'reply'
  | 'done'
  | 'error'

/** 后端 POST /api/chat/stream 推送的单条进度事件 */
export interface ProgressEvent {
  /** 步骤名 */
  step: ProgressStep
  /** 状态：start 开始 / progress 进行中（带 current、total）/ done 完成 / retrying 重试中 / failed 最终失败 */
  status: string
  /** 当前进度（页面/音频的 x） */
  current?: number
  /** 总数（页面/音频的 n）；0 表示拿不到总数，前端不展示 x/n */
  total?: number
  /** 展示给用户的文本（闲聊回复、错误原因等） */
  message?: string
  /** step=topic 时的讲解主题，用于展示「讲解主题：xxx」 */
  topic?: string
  /** step=done 时携带的完整课件数据 */
  data?: ChatResponse
}

/**
 * 调用后端流式接口，逐条回调进度事件（SSE：服务端通过长连接单向推送）。
 * 需要 POST 带请求体，所以用 fetch + ReadableStream 手动解析，而非 EventSource。
 * 网络错误、HTTP 非 200、流被截断都会抛错，由调用方展示失败态。
 */
export async function streamMessage(
  message: string,
  onEvent: (event: ProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })

  if (!response.ok || !response.body) {
    throw new Error(`请求失败（HTTP ${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // 按空行分帧，兼容一次网络包里带多帧、一帧被拆到两个包的情况
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 2)
      if (frame.startsWith('data:')) {
        const payload = frame.slice(5).trim()
        if (payload) {
          try {
            onEvent(JSON.parse(payload) as ProgressEvent)
          } catch {
            // 单帧解析失败不影响后续帧，忽略继续
          }
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
