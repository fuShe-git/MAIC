import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'
import type { PageItem } from './chat'

/** 课件列表里的单条记录（含产物快照，供课件库展示与再编辑） */
export interface CourseItem {
  id: string
  topic: string
  createdAt: string
  status: 'done' | 'partial'
  pageCount: number
  htmlPath: string
  catalogList?: Array<Record<string, unknown>>
  pageList?: PageItem[]
  audioList?: Array<Record<string, unknown>>
  quizList?: Array<Record<string, unknown>>
}

/** 课件详情 */
export function getCourse(courseId: string, signal?: SseSignal): Promise<CourseItem> {
  return fetchJson<CourseItem>(`${API_BASE}/courses/${courseId}`, { signal })
}
