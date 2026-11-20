import { useCallback, useEffect, useState } from 'react'
import { courseHtmlUrl, deleteCourse, listCourses } from '../api/courses'
import type { CourseItem } from '../api/courses'
import './CourseList.css'

interface CourseListProps {
  /** 返回首页 */
  onBack: () => void
}

/**
 * 课件库（新增能力 5）：卡片列表展示已生成的课件，支持打开学习与删除。
 */
export default function CourseList({ onBack }: CourseListProps) {
  const [items, setItems] = useState<CourseItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const pageSize = 10

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await listCourses(page, pageSize)
      setItems(data.items)
      setTotal(data.total)
    } catch {
      setError('课件库加载失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    load()
  }, [load])

  const handleDelete = async (course: CourseItem) => {
    if (!window.confirm(`确认删除课件「${course.topic}」？将同时删除课件文件。`)) return
    try {
      await deleteCourse(course.id)
      load()
    } catch {
      setError('删除失败，请稍后重试')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="course-list-root">
      <div className="course-list-card">
        <div className="course-list-header">
          <button type="button" className="course-list-back" onClick={onBack}>
            ← 返回
          </button>
          <h1>我的课件库</h1>
          <span className="course-list-count">共 {total} 份</span>
        </div>

        {error ? <div className="course-list-error">{error}</div> : null}

        {loading ? (
          <div className="course-list-empty">加载中…</div>
        ) : items.length === 0 ? (
          <div className="course-list-empty">还没有课件，去首页生成第一堂课吧</div>
        ) : (
          <div className="course-list-items">
            {items.map((course) => (
              <div className="course-card" key={course.id}>
                <div className="course-card-main">
                  <div className="course-card-title">{course.topic || '未命名课件'}</div>
                  <div className="course-card-meta">
                    <span className={`course-badge ${course.status}`}>
                      {course.status === 'partial' ? '部分成功' : '已完成'}
                    </span>
                    <span>{course.pageCount} 页</span>
                    <span>{course.createdAt || '—'}</span>
                  </div>
                </div>
                <div className="course-card-actions">
                  <a
                    className="ghost-btn small"
                    href={courseHtmlUrl(course.id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    打开学习
                  </a>
                  <button type="button" className="ghost-btn small danger" onClick={() => handleDelete(course)}>
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {totalPages > 1 ? (
          <div className="course-list-pager">
            <button type="button" className="ghost-btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              type="button"
              className="ghost-btn small"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
