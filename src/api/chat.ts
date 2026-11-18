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
