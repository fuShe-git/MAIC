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

/** 后端产物文件名固定为 uuid4().hex + 扩展名 */
const HTML_NAME_RE = /^[0-9a-f]{32}\.html$/i
const MEDIA_NAME_RE = /^[0-9a-f]{32}\.(?:mp3|mp4)$/i

/** 历史数据里的本地绝对路径 → 同源后端接口地址 */
function artifactUrlFromLocalPath(path: string): string {
  const name = path.replace(/\\/g, '/').split('/').pop() ?? ''
  if (HTML_NAME_RE.test(name)) {
    return `/api/generated/files/${encodeURIComponent(name)}/html`
  }
  if (MEDIA_NAME_RE.test(name)) {
    return `/api/generated/files/${encodeURIComponent(name)}/media`
  }
  return ''
}

/**
 * 把后端返回的路径转成浏览器可访问的 URL。
 *
 * 后端现在直接返回 `/api/generated/files/...` 同源地址，开发（Vite 代理）
 * 和生产（Nginx 反代）都能用；这里只为历史数据中的本地绝对路径做兜底。
 */
export function toFileUrl(path: string): string {
  const trimmed = path.trim()
  if (!trimmed) return ''
  // 已经是 http(s)、data、/@fs、绝对 URL 或站内绝对路径时原样返回
  if (/^(https?:|data:|blob:|\/@fs\/|\/)/i.test(trimmed)) return trimmed
  // Windows 的 file:// 协议（浏览器从 http 页面加载会被拦，这里仅剥掉前缀继续转换）
  const withoutScheme = trimmed.replace(/^file:\/\//i, '')

  const artifactUrl = artifactUrlFromLocalPath(withoutScheme)
  if (artifactUrl) return artifactUrl

  // /@fs 只在 Vite dev server 存在；生产构建下宁可降级，也不产出必然 404 的地址
  if (!import.meta.env.DEV) return ''

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
