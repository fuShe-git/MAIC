import type { PageItem } from '../api/chat'

/**
 * 中间内容展示的降级链产物：
 * html   —— courseHtml 课件 HTML（自带旁白音频，播完自动翻页）
 * video  —— videoUrl 视频
 * pages  —— pageList 逐页 HTML（无音频，仅手动翻页）
 * none   —— 什么产物都没有，只能展示 reply 文本
 */
export type CourseSourceKind = 'html' | 'video' | 'pages' | 'none'

export interface CourseSource {
  kind: CourseSourceKind
  /** kind='html'|'video' 时为可加载的 URL；kind='pages' 时为空串（用 pageList） */
  src: string
}

/**
 * 把后端返回的本地落盘路径转成浏览器可访问的 URL。
 *
 * 后端返回的是 Windows 绝对路径（如 D:\...\server\outputs\videos\x.html），
 * 浏览器无法直接打开；开发环境用 Vite dev server 自带的 `/@fs/<绝对路径>`
 * 文件系统访问机制（同源，保证 iframe 的 contentDocument 可被读取）。
 * 注意：`/@fs/` 仅 dev 模式有效，生产构建需另行配置静态文件服务。
 */
export function toFileUrl(path: string): string {
  const trimmed = path.trim()
  if (!trimmed) return ''
  // 已经是 http(s)、data、/@fs、绝对 URL 或站内绝对路径时原样返回
  if (/^(https?:|data:|blob:|\/@fs\/|\/)/i.test(trimmed)) return trimmed
  // Windows 的 file:// 协议（浏览器从 http 页面加载会被拦，这里仅剥掉前缀继续转换）
  const withoutScheme = trimmed.replace(/^file:\/\//i, '')

  // 反斜杠归一化为正斜杠；Windows 盘符路径前面补 /
  const normalized = withoutScheme.replace(/\\/g, '/')
  const withRoot = /^[a-zA-Z]:\//.test(normalized) ? `/${normalized}` : normalized
  return `/@fs${withRoot}`
}

/**
 * 按降级链选出中间内容展示源：
 * courseHtml → videoUrl → pageList → none。
 * 产物路径为空串视为「未生成」，逐级兜底；
 * exclude 用于把加载失败的类型排除掉，实现失败后自动降级。
 */
export function pickCourseSource(
  input: {
    courseHtml: string
    videoUrl: string
    pageList: PageItem[]
  },
  exclude: CourseSourceKind[] = [],
): CourseSource {
  const htmlUrl = toFileUrl(input.courseHtml)
  if (htmlUrl && !exclude.includes('html')) return { kind: 'html', src: htmlUrl }

  const videoUrl = toFileUrl(input.videoUrl)
  if (videoUrl && !exclude.includes('video')) return { kind: 'video', src: videoUrl }

  const usablePages = input.pageList.filter((page) => page.htmlpage.trim())
  if (usablePages.length > 0 && !exclude.includes('pages')) {
    return { kind: 'pages', src: '' }
  }
  return { kind: 'none', src: '' }
}
