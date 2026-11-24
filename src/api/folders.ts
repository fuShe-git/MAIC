import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'

/** 「我的课堂」文件夹：后端 SQLite 持久化（早期版本存在 localStorage） */
export interface CourseFolder {
  /** 文件夹 id */
  id: string
  /** 文件夹名称（用户输入） */
  name: string
  /** 归入该文件夹的课件 id，按存放顺序 */
  courseIds: string[]
  /** 创建时间（ISO 字符串） */
  createdAt: string
}

export interface FolderListResponse {
  items: CourseFolder[]
}

/** 列出全部文件夹（新建的在前） */
export function listFolders(signal?: SseSignal): Promise<FolderListResponse> {
  return fetchJson<FolderListResponse>(`${API_BASE}/folders`, { signal })
}

/** 新建文件夹；传 courseId 时同时把该课件移进去 */
export function createFolder(name: string, courseId?: string): Promise<CourseFolder> {
  return fetchJson<CourseFolder>(`${API_BASE}/folders`, {
    method: 'POST',
    body: JSON.stringify({ name, courseId: courseId ?? '' }),
  })
}

/** 重命名文件夹 */
export function renameFolder(folderId: string, name: string): Promise<CourseFolder> {
  return fetchJson<CourseFolder>(`${API_BASE}/folders/${encodeURIComponent(folderId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

/** 只删除文件夹本身，里面的课件回到「未分组」 */
export function deleteFolder(folderId: string): Promise<{ deleted: boolean }> {
  return fetchJson<{ deleted: boolean }>(`${API_BASE}/folders/${encodeURIComponent(folderId)}`, {
    method: 'DELETE',
  })
}

/** 设置课件归属；folderId 为空串表示移出所有文件夹 */
export function assignCourseToFolder(
  courseId: string,
  folderId: string,
): Promise<{ courseId: string; folderId: string }> {
  return fetchJson<{ courseId: string; folderId: string }>(`${API_BASE}/folders/assignment`, {
    method: 'PUT',
    body: JSON.stringify({ courseId, folderId }),
  })
}

/**
 * 首次加载文件夹：并发调用共享同一个请求。
 *
 * StrictMode 下 effect 会执行两次，这里做去重，避免首屏重复请求。
 */
let loadInflight: Promise<CourseFolder[]> | null = null

export function ensureFoldersLoaded(): Promise<CourseFolder[]> {
  if (loadInflight) return loadInflight

  loadInflight = listFolders()
    .then((data) => (Array.isArray(data.items) ? data.items : []))
    .finally(() => {
      loadInflight = null
    })
  return loadInflight
}
