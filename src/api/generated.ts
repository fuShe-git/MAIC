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
  /** 用户自定义备注名；空串表示未改名，展示时回退 topic */
  remark?: string
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

/** 修改课件备注名（落库）；传空串表示恢复课件自带标题 */
export function renameGeneratedFile(
  fileName: string,
  name: string,
): Promise<{ id: string; remark: string }> {
  return fetchJson<{ id: string; remark: string }>(
    `${API_BASE}/generated/files/${encodeURIComponent(fileName)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) },
  )
}

/** 删除一份课件：后端会同时清掉 HTML 文件、封面缓存与资产库记录 */
export function deleteGeneratedFile(fileName: string): Promise<{ deleted: boolean }> {
  return fetchJson<{ deleted: boolean }>(
    `${API_BASE}/generated/files/${encodeURIComponent(fileName)}`,
    { method: 'DELETE' },
  )
}

/** 课件 HTML 的同源访问地址；同源才能让播放器读取 iframe 内部文档 */
export function generatedHtmlUrl(fileName: string): string {
  return `${API_BASE}/generated/files/${encodeURIComponent(fileName)}/html`
}

/** 课件首屏封面图地址：后端首次访问时渲染并缓存，前端直接当 <img> 用 */
export function generatedCoverUrl(fileName: string): string {
  return `${API_BASE}/generated/files/${encodeURIComponent(fileName)}/cover`
}
