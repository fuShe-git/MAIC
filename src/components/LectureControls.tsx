import type { CourseSourceKind } from '../utils/courseSource'

export interface LectureControlsProps {
  /** 当前展示类型，决定按钮区形态 */
  kind: CourseSourceKind
  /** 课件内部页码文本，如「2 / 6」 */
  pageLabel: string
  /** 课件是否在播放中（同步内部 #start 按钮文本） */
  playing: boolean
  /** 是否已在第一页 / 最后一页，用于禁用翻页按钮 */
  atFirst: boolean
  atLast: boolean
  /** 逐页兜底模式的当前页下标与总页数 */
  pageIndex: number
  pageTotal: number
  /** 视频模式的播放进度与总时长（秒） */
  videoTime: number
  videoDuration: number
  videoPlaying: boolean
  /** 课件内部上一页 / 播放暂停 / 下一页 */
  onPrev: () => void
  onToggle: () => void
  onNext: () => void
  /** 逐页兜底模式翻页 */
  onPageIndexChange: (index: number) => void
  /** 视频模式拖动进度条 */
  onSeek: (time: number) => void
}

/** 「上一页」左箭头图标 */
function IconPrev() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M15 5 8 12l7 7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** 「下一页」右箭头图标 */
function IconNext() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="m9 5 7 7-7 7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** 「开始/播放」三角形图标 */
function IconPlay() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M8 5.6v12.8a.8.8 0 0 0 1.22.68l10-6.4a.8.8 0 0 0 0-1.36l-10-6.4A.8.8 0 0 0 8 5.6Z" />
    </svg>
  )
}

/** 「暂停」双竖线图标 */
function IconPause() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="6.5" y="5" width="4" height="14" rx="1.2" />
      <rect x="13.5" y="5" width="4" height="14" rx="1.2" />
    </svg>
  )
}

/** 把秒数格式化为 `m:ss`（如 125 -> 2:05）；非法值兜底显示 0:00 */
function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 底部按钮区：按展示类型渲染「上一页 / 开始·暂停 / 下一页 + 页码」或视频进度条 */
export default function LectureControls({
  kind,
  pageLabel,
  playing,
  atFirst,
  atLast,
  pageIndex,
  pageTotal,
  videoTime,
  videoDuration,
  videoPlaying,
  onPrev,
  onToggle,
  onNext,
  onPageIndexChange,
  onSeek,
}: LectureControlsProps) {
  // HTML 课件：上一页 / 开始·暂停 / 下一页 + 页码（首尾页禁用对应按钮）
  if (kind === 'html') {
    return (
      <>
        <button
          className="lp-ctrl"
          type="button"
          disabled={atFirst}
          onClick={onPrev}
          aria-label="上一页"
        >
          <IconPrev />
        </button>
        <button className="lp-ctrl is-primary" type="button" onClick={onToggle}>
          {playing ? <IconPause /> : <IconPlay />}
          <span>{playing ? '暂停' : '开始'}</span>
        </button>
        <button
          className="lp-ctrl"
          type="button"
          disabled={atLast}
          onClick={onNext}
          aria-label="下一页"
        >
          <IconNext />
        </button>
        <span className="lp-page">{pageLabel}</span>
      </>
    )
  }

  // 视频：播放·暂停 + 可拖动进度条 + 时间显示
  if (kind === 'video') {
    return (
      <>
        <button
          className="lp-ctrl is-primary"
          type="button"
          onClick={onToggle}
          aria-label={videoPlaying ? '暂停' : '播放'}
        >
          {videoPlaying ? <IconPause /> : <IconPlay />}
          <span>{videoPlaying ? '暂停' : '开始'}</span>
        </button>
        <input
          className="lp-seek"
          type="range"
          min={0}
          max={videoDuration || 0}
          step={0.1}
          value={videoTime}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label="播放进度"
        />
        <span className="lp-page">
          {formatTime(videoTime)} / {formatTime(videoDuration)}
        </span>
      </>
    )
  }

  // 逐页兜底模式：手动翻页（无旁白音频）
  if (kind === 'pages' && pageTotal > 0) {
    return (
      <>
        <button
          className="lp-ctrl"
          type="button"
          disabled={pageIndex === 0}
          onClick={() => onPageIndexChange(Math.max(0, pageIndex - 1))}
          aria-label="上一页"
        >
          <IconPrev />
        </button>
        <span className="lp-page">
          {pageIndex + 1} / {pageTotal}
        </span>
        <button
          className="lp-ctrl"
          type="button"
          disabled={pageIndex >= pageTotal - 1}
          onClick={() => onPageIndexChange(Math.min(pageTotal - 1, pageIndex + 1))}
          aria-label="下一页"
        >
          <IconNext />
        </button>
        <span className="lp-hint">逐页展示（无旁白音频）</span>
      </>
    )
  }

  // 产物全空：按钮区没有可操作内容，留空避免出现空控制条
  return null
}
