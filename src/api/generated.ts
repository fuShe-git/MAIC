import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'

/** videos 目录中的一份生成课件（由后端扫描落盘文件得到） */
export interface GeneratedCourse {
  /** 文件名去掉扩展名，作为稳定 id */
  id: string
  /** 落盘文件名（含 .html），用于拼 HTML 访问地址 */
  fileName: string
  /** 课件标题，取 HTML 的 <title> */
  topic: string
  /** 文件修改时间（ISO 字符串） */
  createdAt: string
  /** 文件字节数 */
  size: number
}

export interface GeneratedListResponse {
  total: number
  items: GeneratedCourse[]
}

/** 列出 videos 目录下所有生成的课件（倒序） */
export function listGeneratedFiles(signal?: SseSignal): Promise<GeneratedListResponse> {
  return fetchJson<GeneratedListResponse>(`${API_BASE}/generated/files`, { signal })
}

/** 课件 HTML 的同源访问地址；同源才能让播放器读取 iframe 内部文档 */
export function generatedHtmlUrl(fileName: string): string {
  return `${API_BASE}/generated/files/${encodeURIComponent(fileName)}/html`
}
