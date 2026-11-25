import { API_BASE, fetchJson } from './http'

/** PPT 样式配色预览，用于卡片上的小色板。 */
export interface ThemePreviewColors {
  background: string
  primary: string
  accent: string
  text: string
}

/**
 * PPT 样式模板。stylePrompt 会随确认请求回传后端，作为页面生成的视觉规范。
 * 注意：这里的“主题”指课件视觉样式，与页面顶部的明/暗主题切换是两套概念。
 */
export interface ThemeTemplate {
  id: string
  name: string
  description: string
  previewColors: ThemePreviewColors
  stylePrompt: string
}

export interface ThemeListResponse {
  themes: ThemeTemplate[]
  /** true 表示后端生成失败，返回的是内置兜底样式 */
  degraded?: boolean
}

export interface ThemeRequest {
  /** 用户最初输入的课程主题 */
  message: string
  /** 用户对样式的修改意见；为空时表示首次生成 */
  feedback?: string
  /** 重新生成时带上当前候选，便于模型定向调整 */
  currentThemes?: ThemeTemplate[]
  modelId?: string
}

/** 后端不可用时的前端兜底：保证 UI 仍可展示、选择并继续生成。 */
export const FALLBACK_THEMES: ThemeTemplate[] = [
  {
    id: 'fallback-ink-blue',
    name: '深蓝课堂',
    description: '沉稳的蓝白 academic 风格，适合知识点密集的讲解。',
    previewColors: { background: '#F4F7FF', primary: '#2F5BEA', accent: '#6C8CFF', text: '#1B2440' },
    stylePrompt:
      '使用深蓝与白色为主的 academic 版式：浅蓝背景 #F4F7FF，主色 #2F5BEA，标题加粗，信息卡片为半透明白色并带细蓝边，底部横幅使用主色。',
  },
  {
    id: 'fallback-forest',
    name: '青绿笔记',
    description: '清爽的青绿笔记风，适合循序渐进的基础教学。',
    previewColors: { background: '#F2FBF7', primary: '#147D64', accent: '#4FC3A1', text: '#17342C' },
    stylePrompt:
      '使用青绿笔记风格：背景 #F2FBF7，主色 #147D64，卡片带左侧色条与浅绿描边，标题清新有力，底部横幅用深绿色。',
  },
  {
    id: 'fallback-dusk',
    name: '暮紫剧场',
    description: '带有舞台感的紫色渐变风格，适合故事化与场景化课程。',
    previewColors: { background: '#F7F3FF', primary: '#6B3DE0', accent: '#B69CFF', text: '#2B1B4D' },
    stylePrompt:
      '使用暮紫剧场风格：背景 #F7F3FF，主色 #6B3DE0，标题带轻微渐变，卡片为白色加紫色阴影，底部横幅使用紫到深紫的渐变。',
  },
]

/** 获取或重新生成 PPT 样式模板；普通 JSON 请求，不走 SSE。 */
export function generateThemes(
  payload: ThemeRequest,
  signal?: AbortSignal,
): Promise<ThemeListResponse> {
  return fetchJson<ThemeListResponse>(`${API_BASE}/chat/themes`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}
