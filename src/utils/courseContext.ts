import type { PageItem } from '../api/chat'

/** 发给后端的单页课件正文（不带 HTML，避免重复传输大体积内容） */
export interface CoursePageContext {
  ind: number
  context: string
}

/** 会话内历史消息的最小结构 */
export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

/** 把接口返回的任意页面快照规整成播放器可用的 PageItem */
export function toPageItems(raw: unknown): PageItem[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((row, index) => {
    if (!row || typeof row !== 'object') return []
    const item = row as Partial<PageItem>
    const ind = Number(item.ind)
    if (!Number.isFinite(ind)) return []
    return [
      {
        ind,
        context: typeof item.context === 'string' ? item.context : '',
        htmlpage: typeof item.htmlpage === 'string' ? item.htmlpage : '',
      },
    ]
  })
}

/** 取全部讲解正文，首问一次性附带整份课件 */
export function buildCoursePages(pageList: PageItem[]): CoursePageContext[] {
  return pageList
    .map((page, index) => ({
      ind: Number.isFinite(page.ind) ? page.ind : index + 1,
      context: (page.context ?? '').trim(),
    }))
    .filter((page) => page.context.length > 0)
}

/** 从「3 / 8」里解析当前页码，用于界面提示 */
export function parsePageNumber(pageLabel: string): number | null {
  const match = /(^|\D)(\d+)\s*\//.exec(pageLabel ?? '')
  if (!match) return null
  const value = Number(match[2])
  return Number.isFinite(value) ? value : null
}

/** 规整多轮历史：过滤空消息并限制最近轮数 */
export function buildHistory(messages: ChatTurn[], maxTurns = 8): ChatTurn[] {
  return messages
    .map((item) => ({ role: item.role, content: (item.content ?? '').trim() }))
    .filter((item) => item.content.length > 0)
    .slice(-maxTurns * 2)
}
