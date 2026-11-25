import { useEffect, useRef } from 'react'
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react'
import { useI18n } from '../i18n/context'
import type { OutlineItem } from '../utils/courseContext'
import './LectureOutline.css'

export interface LectureOutlineProps {
  /** 按课件页顺序排列的大纲 */
  items: OutlineItem[]
  /** 当前播放页（1 起），用于高亮与自动滚动 */
  currentPage: number | null
  /** 当前大纲宽度，由播放器统一管理 */
  width: number
  /** 拖动右边缘调整宽度 */
  onWidthChange: (width: number) => void
  /** 课件尚未就绪时禁止跳页 */
  disabled?: boolean
  onSelect: (page: number) => void
  /** 收起大纲；窄屏时也用于关闭抽屉 */
  onCollapse: () => void
}

const MIN_WIDTH = 168
const MAX_WIDTH = 380
/** 缩略图 iframe 的固定画布宽度，缩略框宽度由当前栏宽反推 */
const THUMB_CANVAS_WIDTH = 1170

function padPage(page: number): string {
  return String(page).padStart(2, '0')
}

function OutlineThumb({ item }: { item: OutlineItem }) {
  if (!item.htmlpage.trim()) {
    return (
      <span className="lp-outline-thumb is-empty" aria-hidden="true">
        <span>第 {item.page} 页</span>
      </span>
    )
  }

  return (
    <span className="lp-outline-thumb">
      {/* sandbox 置空：缩略图只做静态预览，禁止脚本与交互，避免每页脚本重复执行 */}
      <iframe
        title={`第 ${item.page} 页预览`}
        srcDoc={item.htmlpage}
        sandbox=""
        tabIndex={-1}
        aria-hidden="true"
        loading="lazy"
      />
    </span>
  )
}

/** 学习页左侧的课件大纲：编号 + 缩略预览 + 标题，当前页高亮 */
export default function LectureOutline({
  items,
  currentPage,
  width,
  onWidthChange,
  disabled = false,
  onSelect,
  onCollapse,
}: LectureOutlineProps) {
  const { t } = useI18n()
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const listRef = useRef<HTMLOListElement>(null)
  const widthDragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  // 播放页变化时只滚动到最近可见位置，避免打断用户正在浏览的列表
  useEffect(() => {
    if (currentPage == null) return
    const index = items.findIndex((item) => item.page === currentPage)
    const node = index >= 0 ? itemRefs.current[index] : null
    node?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [currentPage, items])

  // 缩略图按当前栏宽等比缩放；拖拽时同步重算，保证始终铺满且不裁切
  useEffect(() => {
    const list = listRef.current
    if (!list) return
    const updateScale = () => {
      const item = list.querySelector<HTMLElement>('.lp-outline-item')
      if (!item) return
      const style = getComputedStyle(item)
      const inner =
        item.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      if (!Number.isFinite(inner) || inner <= 0) return
      const scale = Math.min(0.3, Math.max(0.08, inner / THUMB_CANVAS_WIDTH))
      list.style.setProperty('--lp-thumb-scale', scale.toFixed(4))
    }
    updateScale()
    const observer = new ResizeObserver(updateScale)
    observer.observe(list)
    return () => observer.disconnect()
  }, [items.length])

  function handleResizePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    widthDragRef.current = { startX: event.clientX, startWidth: width }
  }

  function handleResizePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = widthDragRef.current
    if (!drag) return
    const next = drag.startWidth + (event.clientX - drag.startX)
    onWidthChange(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(next))))
  }

  function handleResizePointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    widthDragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function handleResizeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const delta = event.key === 'ArrowRight' ? 12 : -12
    onWidthChange(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width + delta)))
  }

  return (
    <>
      <button
        className="lp-outline-scrim"
        type="button"
        tabIndex={-1}
        aria-label="关闭课件大纲"
        onClick={onCollapse}
      />
      <aside className="lp-outline" aria-label="课件大纲">
        <div className="lp-outline-head">
          <div className="lp-outline-head-text">
            <h2 className="lp-outline-heading">{t('outline.title')}</h2>
          </div>
          <span className="lp-outline-count">
            {currentPage ?? 1}
            <i>/</i>
            {items.length}
          </span>
          <button
            className="lp-outline-collapse"
            type="button"
            aria-label="收起课件大纲"
            title="收起大纲"
            onClick={onCollapse}
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M14 7l-5 5 5 5"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        <ol className="lp-outline-list" ref={listRef}>
          {items.map((item, index) => {
            const active = currentPage === item.page
            return (
              <li
                key={`${item.ind}-${item.page}`}
                className="lp-outline-entry"
                style={{ '--outline-index': index } as CSSProperties}
              >
                <button
                  ref={(node) => {
                    itemRefs.current[index] = node
                  }}
                  className={`lp-outline-item${active ? ' is-active' : ''}`}
                  type="button"
                  disabled={disabled}
                  aria-current={active ? 'page' : undefined}
                  aria-label={`第 ${item.page} 页：${item.title}`}
                  onClick={() => onSelect(item.page)}
                >
                  <span className="lp-outline-num">{padPage(item.page)}</span>
                  <span className="lp-outline-label" title={item.title}>
                    {item.title}
                  </span>
                  <OutlineThumb item={item} />
                </button>
              </li>
            )
          })}
        </ol>

        <div
          className="lp-outline-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖动调整课件大纲宽度"
          aria-valuenow={width}
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={MAX_WIDTH}
          tabIndex={0}
          title="拖动调整宽度"
          onPointerDown={handleResizePointerDown}
          onPointerMove={handleResizePointerMove}
          onPointerUp={handleResizePointerEnd}
          onPointerCancel={handleResizePointerEnd}
          onKeyDown={handleResizeKeyDown}
        />
      </aside>
    </>
  )
}
