import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n/context'
import type { CourseSourceKind } from '../utils/courseSource'
import {
  IconAutoPlay,
  IconDiscussion,
  IconExpandLess,
  IconExpandMore,
  IconFullscreen,
  IconMute,
  IconNext,
  IconOutline,
  IconPause,
  IconPlay,
  IconPrev,
  IconVolume,
} from './LectureIcons'

export interface LectureControlsProps {
  kind: CourseSourceKind
  pageLabel: string
  /** 有分页课件时，在底部控制条显示大纲开关 */
  outlineAvailable?: boolean
  outlineOpen?: boolean
  onToggleOutline?: () => void
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
  outlineAvailable = false,
  outlineOpen = false,
  onToggleOutline,
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
  const { t } = useI18n()
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
        {t('controls.rewatch')}
      </button>
    )
  } else if (kind === 'html') {
    center = (
      <>
        <button
          className="lp-ctrl"
          type="button"
          disabled={atFirst}
          onClick={onPrev}
          aria-label={t('controls.prev')}
          title={t('controls.prev')}
        >
          <IconPrev />
        </button>
        <button
          className="lp-ctrl"
          type="button"
          onClick={onToggle}
          aria-label={playing ? t('controls.pause') : t('controls.start')}
          title={playing ? t('controls.pause') : t('controls.start')}
        >
          {playing ? <IconPause /> : <IconPlay />}
        </button>
        <button
          className="lp-ctrl"
          type="button"
          disabled={atLast}
          onClick={onNext}
          aria-label={t('controls.next')}
          title={t('controls.next')}
        >
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
          aria-label={videoPlaying ? t('controls.pause') : t('controls.play')}
          title={videoPlaying ? t('controls.pause') : t('controls.play')}
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
          aria-label={t('controls.progress')}
        />
        <span className="lp-page">{formatTime(videoTime)} / {formatTime(videoDuration)}</span>
      </>
    )
  } else if (kind === 'pages' && pageTotal > 0) {
    center = (
      <>
        <button
          className="lp-ctrl"
          type="button"
          disabled={pageIndex === 0}
          onClick={() => onPageIndexChange(Math.max(0, pageIndex - 1))}
          aria-label={t('controls.prev')}
          title={t('controls.prev')}
        >
          <IconPrev />
        </button>
        <span className="lp-page">{pageIndex + 1} / {pageTotal}</span>
        <button
          className="lp-ctrl"
          type="button"
          disabled={pageIndex >= pageTotal - 1}
          onClick={() => onPageIndexChange(Math.min(pageTotal - 1, pageIndex + 1))}
          aria-label={t('controls.next')}
          title={t('controls.next')}
        >
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
          aria-label={t('controls.rate')}
          aria-haspopup="listbox"
          aria-expanded={rateOpen}
          title={t('controls.rate')}
        >
          {rate}x
        </button>
        {rateOpen && (
          <div className="lp-rate-menu" role="listbox" aria-label={t('controls.rate')}>
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
          aria-label={volume === 0 ? t('controls.unmute') : t('controls.mute')}
          title={volume === 0 ? t('controls.unmute') : t('controls.mute')}
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
            aria-label={t('controls.volume')}
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
        {outlineAvailable && (
          <button
            className={`lp-ctrl is-icon is-outline${outlineOpen ? ' is-active' : ''}`}
            type="button"
            onClick={onToggleOutline}
            aria-label={outlineOpen ? t('controls.collapseCourseOutline') : t('controls.expandCourseOutline')}
            aria-expanded={outlineOpen}
            title={outlineOpen ? t('controls.collapseOutline') : t('controls.expandOutline')}
          >
            <IconOutline />
          </button>
        )}
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
          aria-label={autoPlay ? t('controls.autoplayOff') : t('controls.autoplayOn')}
          aria-pressed={autoPlay}
          title={autoPlay ? t('controls.autoplayOff') : t('controls.autoplayOn')}
        >
          <IconAutoPlay />
        </button>

        <button
          className={`lp-ctrl is-icon${isFullscreen ? ' is-active' : ''}`}
          type="button"
          onClick={onToggleFullscreen}
          disabled={kind === 'none'}
          aria-label={isFullscreen ? t('controls.exitFullscreen') : t('controls.fullscreen')}
          title={isFullscreen ? t('controls.exitFullscreen') : t('controls.fullscreen')}
        >
          <IconFullscreen />
        </button>

        <button
          className="lp-ctrl is-icon"
          type="button"
          onClick={onToggleLower}
          aria-label={lowerCollapsed ? t('controls.expandTranscript') : t('controls.collapseTranscript')}
          aria-expanded={!lowerCollapsed}
          title={lowerCollapsed ? t('controls.expandTranscript') : t('controls.collapseTranscript')}
        >
          {lowerCollapsed ? <IconExpandMore /> : <IconExpandLess />}
        </button>

        <button
          className={`lp-ctrl is-icon${discussionOpen ? ' is-active' : ''}`}
          type="button"
          onClick={onToggleDiscussion}
          aria-label={discussionOpen ? t('controls.collapseDiscussion') : t('controls.expandDiscussion')}
          title={discussionOpen ? t('controls.collapseDiscussion') : t('controls.expandDiscussion')}
        >
          <IconDiscussion />
        </button>
      </div>
    </div>
  )
}
