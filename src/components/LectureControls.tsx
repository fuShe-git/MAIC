import { useEffect, useRef, useState } from 'react'
import type { CourseSourceKind } from '../utils/courseSource'
import {
  IconAutoPlay,
  IconDiscussion,
  IconExpandLess,
  IconExpandMore,
  IconFullscreen,
  IconMute,
  IconNext,
  IconPause,
  IconPlay,
  IconPrev,
  IconVolume,
} from './LectureIcons'

export interface LectureControlsProps {
  kind: CourseSourceKind
  pageLabel: string
  playing: boolean
  atFirst: boolean
  atLast: boolean
  pageIndex: number
  pageTotal: number
  videoTime: number
  videoDuration: number
  videoPlaying: boolean
  quizMode: boolean
  onRewatch: () => void
  discussionOpen: boolean
  onToggleDiscussion: () => void
  autoPlay: boolean
  onToggleAutoPlay: () => void
  rate: number
  onRateChange: (rate: number) => void
  volume: number
  onVolumeChange: (volume: number) => void
  isFullscreen: boolean
  onToggleFullscreen: () => void
  lowerCollapsed: boolean
  onToggleLower: () => void
  audioEnabled: boolean
  onPrev: () => void
  onToggle: () => void
  onNext: () => void
  onPageIndexChange: (index: number) => void
  onSeek: (time: number) => void
  /** bar：讲解词面板内的整条控制条；overlay：全屏时悬浮在中部的精简按钮组 */
  variant?: 'bar' | 'overlay'
}

// 菜单向上弹出，按从高到低排列，让高倍速显示在最上面
const RATES = [2, 1.5, 1.25, 1, 0.75]

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 课件下方的细控制条：左侧页码、中部倍速音量与播放控制、右侧播放设置与对话入口 */
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
  quizMode,
  onRewatch,
  discussionOpen,
  onToggleDiscussion,
  autoPlay,
  onToggleAutoPlay,
  rate,
  onRateChange,
  volume,
  onVolumeChange,
  isFullscreen,
  onToggleFullscreen,
  lowerCollapsed,
  onToggleLower,
  audioEnabled,
  onPrev,
  onToggle,
  onNext,
  onPageIndexChange,
  onSeek,
  variant = 'bar',
}: LectureControlsProps) {
  const [rateOpen, setRateOpen] = useState(false)
  const rateRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!rateOpen) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rateRef.current?.contains(event.target as Node)) setRateOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setRateOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [rateOpen])

  let center
  if (quizMode) {
    center = (
      <button className="lp-ctrl is-primary" type="button" onClick={onRewatch}>
        重新观看
      </button>
    )
  } else if (kind === 'html') {
    center = (
      <>
        <button className="lp-ctrl" type="button" disabled={atFirst} onClick={onPrev} aria-label="上一页" title="上一页">
          <IconPrev />
        </button>
        <button
          className="lp-ctrl"
          type="button"
          onClick={onToggle}
          aria-label={playing ? '暂停' : '开始'}
          title={playing ? '暂停' : '开始'}
        >
          {playing ? <IconPause /> : <IconPlay />}
        </button>
        <button className="lp-ctrl" type="button" disabled={atLast} onClick={onNext} aria-label="下一页" title="下一页">
          <IconNext />
        </button>
      </>
    )
  } else if (kind === 'video') {
    center = (
      <>
        <button
          className="lp-ctrl"
          type="button"
          onClick={onToggle}
          aria-label={videoPlaying ? '暂停' : '播放'}
          title={videoPlaying ? '暂停' : '播放'}
        >
          {videoPlaying ? <IconPause /> : <IconPlay />}
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
        <span className="lp-page">{formatTime(videoTime)} / {formatTime(videoDuration)}</span>
      </>
    )
  } else if (kind === 'pages' && pageTotal > 0) {
    center = (
      <>
        <button className="lp-ctrl" type="button" disabled={pageIndex === 0} onClick={() => onPageIndexChange(Math.max(0, pageIndex - 1))} aria-label="上一页" title="上一页">
          <IconPrev />
        </button>
        <span className="lp-page">{pageIndex + 1} / {pageTotal}</span>
        <button className="lp-ctrl" type="button" disabled={pageIndex >= pageTotal - 1} onClick={() => onPageIndexChange(Math.min(pageTotal - 1, pageIndex + 1))} aria-label="下一页" title="下一页">
          <IconNext />
        </button>
      </>
    )
  }

  const settings = (
    <>
      <div className="lp-rate" ref={rateRef}>
        <button
          className="lp-ctrl"
          type="button"
          onClick={() => setRateOpen((value) => !value)}
          disabled={!audioEnabled}
          aria-label="播放倍速"
          aria-haspopup="listbox"
          aria-expanded={rateOpen}
          title="播放倍速"
        >
          {rate}x
        </button>
        {rateOpen && (
          <div className="lp-rate-menu" role="listbox" aria-label="播放倍速">
            {RATES.map((value) => (
              <button
                key={value}
                className={`lp-rate-option${value === rate ? ' is-active' : ''}`}
                type="button"
                role="option"
                aria-selected={value === rate}
                onClick={() => {
                  onRateChange(value)
                  setRateOpen(false)
                }}
              >
                {value}x
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="lp-volume">
        <button
          className="lp-ctrl is-icon"
          type="button"
          onClick={() => onVolumeChange(volume > 0 ? 0 : 1)}
          disabled={!audioEnabled}
          aria-label={volume === 0 ? '取消静音' : '静音'}
          title={volume === 0 ? '取消静音' : '静音'}
        >
          {volume === 0 ? <IconMute /> : <IconVolume />}
        </button>
        <div className="lp-volume-panel">
          <span className="lp-volume-value">{Math.round(volume * 100)}</span>
          <input
            className="lp-volume-range"
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={volume}
            onChange={(event) => onVolumeChange(Number(event.target.value))}
            disabled={!audioEnabled}
            aria-label="音量"
          />
        </div>
      </div>
    </>
  )

  // 全屏浮层只需要「倍速 + 音量 + 翻页/播放」，容器与显隐由 LecturePlayer 控制
  if (variant === 'overlay') {
    return (
      <>
        {settings}
        {center}
      </>
    )
  }

  return (
    <div className="lp-statusbar">
      <div className="lp-bar-left">
        <span className="lp-page">{pageLabel}</span>
      </div>

      <div className="lp-bar-center">
        {settings}
        {center}
      </div>

      <div className="lp-bar-right">
        <button
          className={`lp-ctrl is-icon${autoPlay ? ' is-active' : ''}`}
          type="button"
          onClick={onToggleAutoPlay}
          disabled={!audioEnabled}
          aria-label={autoPlay ? '关闭自动播放' : '开启自动播放'}
          aria-pressed={autoPlay}
          title={autoPlay ? '关闭自动播放' : '开启自动播放'}
        >
          <IconAutoPlay />
        </button>

        <button
          className={`lp-ctrl is-icon${isFullscreen ? ' is-active' : ''}`}
          type="button"
          onClick={onToggleFullscreen}
          disabled={kind === 'none'}
          aria-label={isFullscreen ? '退出全屏' : '课件全屏'}
          title={isFullscreen ? '退出全屏' : '课件全屏'}
        >
          <IconFullscreen />
        </button>

        <button
          className="lp-ctrl is-icon"
          type="button"
          onClick={onToggleLower}
          aria-label={lowerCollapsed ? '展开讲解词' : '收起讲解词'}
          aria-expanded={!lowerCollapsed}
          title={lowerCollapsed ? '展开讲解词' : '收起讲解词'}
        >
          {lowerCollapsed ? <IconExpandMore /> : <IconExpandLess />}
        </button>

        <button
          className={`lp-ctrl is-icon${discussionOpen ? ' is-active' : ''}`}
          type="button"
          onClick={onToggleDiscussion}
          aria-label={discussionOpen ? '收起对话' : '展开对话'}
          title={discussionOpen ? '收起对话' : '展开对话'}
        >
          <IconDiscussion />
        </button>
      </div>
    </div>
  )
}
