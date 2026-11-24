import { useRef } from 'react'
import type { PointerEvent as ReactPointerEvent, ReactNode, RefObject } from 'react'

interface LectureTranscriptProps {
  text: string
  rootRef: RefObject<HTMLDivElement | null>
  height: number
  onHeightCommit: (height: number) => void
  onCollapse: () => void
  /** 从折叠状态拖出时回调，携带初始展开高度 */
  onExpand?: (height: number) => void
  /** 是否处于折叠状态（保留顶部把手，可再次拖出） */
  collapsed?: boolean
  /** 面板顶部工具栏（播放控制条），占据布局空间而非悬浮 */
  toolbar?: ReactNode
}

const MIN_HEIGHT = 120
const COLLAPSE_THRESHOLD = 24
/** 折叠形态总高：与 46px 控制条齐平（含 1px 顶边框），拖动收缩到该高度即不再继续变小 */
const COLLAPSED_HEIGHT = 47
/** 折叠状态下向上拖动超过该距离才开始展开 */
const EXPAND_THRESHOLD = 4

interface DragState {
  startY: number
  startHeight: number
  maxHeight: number
  height: number
  collapse: boolean
  fromCollapsed: boolean
  expanded: boolean
}

/** 左下讲解词面板：顶部手柄可拖拽改高度，拖到最小以下折叠，折叠后仍可拖出 */
export default function LectureTranscript({
  text,
  rootRef,
  height,
  onHeightCommit,
  onCollapse,
  onExpand,
  collapsed = false,
  toolbar,
}: LectureTranscriptProps) {
  const dragRef = useRef<DragState | null>(null)

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      startY: event.clientY,
      startHeight: collapsed ? 0 : height,
      maxHeight: Math.max(240, Math.round(window.innerHeight * 0.35)),
      height: collapsed ? 0 : height,
      collapse: collapsed,
      fromCollapsed: collapsed,
      expanded: !collapsed,
    }
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    const delta = drag.startY - event.clientY

    // 折叠状态：向上拖动超过阈值后先展开面板，再继续跟随拖动
    if (drag.fromCollapsed && !drag.expanded) {
      if (delta < EXPAND_THRESHOLD) return
      drag.expanded = true
      drag.collapse = false
      drag.startHeight = MIN_HEIGHT
      const initial = Math.min(drag.maxHeight, Math.max(MIN_HEIGHT, MIN_HEIGHT + delta))
      drag.height = initial
      onExpand?.(initial)
    }

    const raw = drag.startHeight + delta
    // 拖动中高度连续跟随鼠标，最小收缩到折叠形态高度，避免面板（含控制条）中途消失
    drag.collapse = raw < MIN_HEIGHT - COLLAPSE_THRESHOLD
    drag.height = Math.min(drag.maxHeight, Math.max(COLLAPSED_HEIGHT, raw))
    rootRef.current?.style.setProperty('--lp-lower-h', `${drag.height}px`)
  }

  const handlePointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }

    // 折叠状态下没有实际拖出：保持折叠
    if (drag.fromCollapsed && !drag.expanded) {
      rootRef.current?.style.setProperty('--lp-lower-h', `${COLLAPSED_HEIGHT}px`)
      return
    }

    if (drag.collapse) {
      // 收起：高度停在折叠形态高度后再切换折叠布局，避免松手瞬间跳变
      rootRef.current?.style.setProperty('--lp-lower-h', `${COLLAPSED_HEIGHT}px`)
      onCollapse()
      return
    }

    // 保留最终高度：若直接移除变量，高度未变化时父级 state 不会更新，
    // 变量缺失会让面板回退到默认高度（拖动后回弹）
    const finalHeight = Math.max(MIN_HEIGHT, drag.height)
    rootRef.current?.style.setProperty('--lp-lower-h', `${finalHeight}px`)
    onHeightCommit(finalHeight)
  }

  return (
    <section className={`lp-lower${collapsed ? ' is-collapsed' : ''}`} aria-label="讲解词">
      <div
        className="lp-resize-handle"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整讲解词区域高度"
        aria-valuenow={collapsed ? 0 : height}
        title="拖动调整高度"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
      />
      {toolbar}
      {!collapsed && (
        <div className="lp-lower-body">
          {text ? (
            <div className="lp-transcript">
              <span className="lp-transcript-avatar" aria-hidden="true">
                讲
              </span>
              <p className="lp-transcript-bubble">{text}</p>
            </div>
          ) : (
            <p className="lp-transcript-empty">当前页暂无讲解词</p>
          )}
        </div>
      )}
    </section>
  )
}
