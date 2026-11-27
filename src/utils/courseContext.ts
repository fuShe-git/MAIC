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
  return raw.flatMap((row) => {
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

/** 从「3 / 8」里解析总页数；格式不合法时返回 null */
export function parsePageTotal(pageLabel: string): number | null {
  const match = /\/(\d+)\s*$/.exec(pageLabel ?? '')
  if (!match) return null
  const value = Number(match[1])
  return Number.isFinite(value) && value > 0 ? value : null
}

/** 播放器左侧大纲的一页 */
export interface OutlineItem {
  /** 后端页面编号，用于对齐 pageList */
  ind: number
  /** 从 1 开始的展示页序，与课件内部页码一致 */
  page: number
  /** 从 context 首行提取的短标题 */
  title: string
  /** 该页 HTML 去掉脚本后的静态快照，用作缩略预览 */
  htmlpage: string
}

/**
 * 缩略图只需要静态画面：去掉 <script>，避免 sandbox 拦截脚本后在控制台刷错误，
 * 顺带减少每页重复解析脚本的开销。
 */
function toThumbHtml(html: string): string {
  return html.replace(/<script\b[\s\S]*?<\/script>/gi, '')
}

/**
 * 把 pageList 转成左侧大纲数据。
 * 后端把每个模块写成「标题：讲稿」的多行 context，首行冒号前即页面标题；
 * 取不到标题时退回「第 N 页」，不额外依赖大纲接口。
 */
export function buildOutlineItems(pageList: PageItem[]): OutlineItem[] {
  return pageList.map((page, index) => {
    const pageNo = index + 1
    const firstLine = (page.context ?? '').trim().split(/\r?\n/)[0] ?? ''
    const colon = firstLine.search(/[：:]/)
    const rawTitle = (colon >= 0 ? firstLine.slice(0, colon) : firstLine)
      .replace(/^#+\s*/, '')
      .replace(/^\s*\d+\s*[.、)]\s*/, '')
      .replace(/\*\*/g, '')
      .trim()
    const ind = Number(page.ind)
    return {
      ind: Number.isFinite(ind) ? ind : pageNo,
      page: pageNo,
      title: rawTitle || `第 ${pageNo} 页`,
      htmlpage: toThumbHtml(page.htmlpage ?? ''),
    }
  })
}

/** 规整多轮历史：过滤空消息并限制最近轮数 */
export function buildHistory(messages: ChatTurn[], maxTurns = 8): ChatTurn[] {
  return messages
    .map((item) => ({ role: item.role, content: (item.content ?? '').trim() }))
    .filter((item) => item.content.length > 0)
    .slice(-maxTurns * 2)
}
