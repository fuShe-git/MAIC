import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'

/** 课件列表里的单条记录（含产物快照，供课件库展示与再编辑） */
export interface CourseItem {
  id: string
  topic: string
  createdAt: string
  status: 'done' | 'partial'
  pageCount: number
  htmlPath: string
  catalogList?: Array<Record<string, unknown>>
  pageList?: Array<Record<string, unknown>>
  audioList?: Array<Record<string, unknown>>
  quizList?: Array<Record<string, unknown>>
}

export interface CourseListResponse {
  total: number
  page: number
  pageSize: number
  items: CourseItem[]
}

/** 分页拉取课件列表（倒序，新课件在前） */
export function listCourses(
  page = 1,
  pageSize = 10,
  signal?: SseSignal,
): Promise<CourseListResponse> {
  return fetchJson<CourseListResponse>(
    `${API_BASE}/courses?page=${page}&page_size=${pageSize}`,
    { signal },
  )
}

/** 课件详情 */
export function getCourse(courseId: string, signal?: SseSignal): Promise<CourseItem> {
  return fetchJson<CourseItem>(`${API_BASE}/courses/${courseId}`, { signal })
}

/** 删除课件（同时删除落盘的课件 HTML 文件） */
export function deleteCourse(courseId: string, signal?: SseSignal): Promise<{ deleted: boolean }> {
  return fetchJson<{ deleted: boolean }>(`${API_BASE}/courses/${courseId}`, {
    method: 'DELETE',
    signal,
  })
}

/** 课件 HTML 的直开地址（新窗口打开即可学习） */
export function courseHtmlUrl(courseId: string): string {
  return `${API_BASE}/courses/${courseId}/html`
}
