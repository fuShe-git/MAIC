import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PageItem } from '../api/chat'
import { pickCourseSource } from '../utils/courseSource'
import type { CourseSourceKind } from '../utils/courseSource'
import LectureControls from './LectureControls'
import './LecturePlayer.css'

export interface LecturePlayerProps {
  /** 识别出的讲解主题，显示在左上角标题 */
  topic: string
  /** 每个模块的页面 HTML，courseHtml 缺失时逐页兜底 */
  pageList: PageItem[]
  /** 组装后的课件 HTML 落盘路径，优先展示 */
  courseHtml: string
  /** 导出的 MP4 落盘路径，课件缺失时兜底 */
  videoUrl: string
  /** 后端回复文本，产物全空时兜底展示 */
  reply: string
  /** 返回主页面 */
  onBack: () => void
}

/** 课件 HTML 体积大（1~2MB 含 base64 音频），超过该时长仍未加载视为失败并降级 */
const LOAD_TIMEOUT_MS = 15_000
/** 轮询课件内部页码 / 播放态的间隔 */
const POLL_MS = 400

export default function LecturePlayer({
  topic,
  pageList,
  courseHtml,
  videoUrl,
  reply,
  onBack,
}: LecturePlayerProps) {
  /** 加载失败过的展示类型，pickCourseSource 会跳过它们逐级降级 */
  const [failed, setFailed] = useState<CourseSourceKind[]>([])
  const [frameReady, setFrameReady] = useState(false)
  const [pageLabel, setPageLabel] = useState('1 / 1')
  const [playing, setPlaying] = useState(false)
  /** 课件已进入课后练习：底部控制区切换为「重新观看」 */
  const [quizMode, setQuizMode] = useState(false)
  /** 逐页兜底模式下的当前页下标 */
  const [pageIndex, setPageIndex] = useState(0)
  const [videoTime, setVideoTime] = useState(0)
  const [videoDuration, setVideoDuration] = useState(0)
  const [videoPlaying, setVideoPlaying] = useState(false)

  const frameRef = useRef<HTMLIFrameElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  const source = useMemo(
    () => pickCourseSource({ courseHtml, videoUrl, pageList }, failed),
    [courseHtml, videoUrl, pageList, failed],
  )

  const usablePages = useMemo(
    () => pageList.filter((page) => page.htmlpage.trim()),
    [pageList],
  )

  const markFailed = useCallback((kind: CourseSourceKind) => {
    setFailed((prev) => (prev.includes(kind) ? prev : [...prev, kind]))
  }, [])

  // 切换展示源时复位加载态
  useEffect(() => {
    setFrameReady(false)
    setPageLabel('1 / 1')
    setPlaying(false)
    setPageIndex(0)
    setQuizMode(false)
  }, [source.kind, source.src])

  // 课件 HTML 长时间未加载成功 → 判定失败并降级
  useEffect(() => {
    if (source.kind !== 'html' || frameReady) return
    const timer = window.setTimeout(() => markFailed('html'), LOAD_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [source.kind, source.src, frameReady, markFailed])

  /** 从课件内部读取页码与播放态，同步到底部按钮区 */
  const syncFromFrame = useCallback(() => {
    const doc = frameRef.current?.contentDocument
    if (!doc) return
    const num = doc.getElementById('pageNum')?.textContent?.trim()
    if (num) setPageLabel(num)
    const start = doc.getElementById('start')?.textContent?.trim()
    if (start) setPlaying(start === '暂停')
    // 课件脚本在进入练习时给 body 打标记，前端据此切换底部控制区
    setQuizMode(doc.body.classList.contains('quiz-mode'))
  }, [])

  // 课件就绪后轮询内部状态（卸载即清理，防止内存泄漏）
  useEffect(() => {
    if (source.kind !== 'html' || !frameReady) return
    syncFromFrame()
    const timer = window.setInterval(syncFromFrame, POLL_MS)
    return () => window.clearInterval(timer)
  }, [source.kind, frameReady, syncFromFrame])

  const handleFrameLoad = useCallback(() => {
    const doc = frameRef.current?.contentDocument
    // 跨域拿不到文档、或加载到的是错误页（没有播放按钮）都算失败
    if (!doc || !doc.getElementById('start')) {
      markFailed('html')
      return
    }
    // 内部原生按钮收编到底部按钮区：隐藏而非删除，保留 DOM 供程序化 click
    const controls = doc.querySelector('.controls') as HTMLElement | null
    if (controls) controls.style.display = 'none'
    setFrameReady(true)
    syncFromFrame()
  }, [markFailed, syncFromFrame])

  /** 触发课件内部按钮（脚本是 IIFE，未暴露全局函数，只能点 DOM） */
  const clickInside = useCallback(
    (id: 'prev' | 'start' | 'next') => {
      const doc = frameRef.current?.contentDocument
      if (!doc) {
        markFailed('html')
        return
      }
      doc.getElementById(id)?.click()
      window.setTimeout(syncFromFrame, 0)
    },
    [markFailed, syncFromFrame],
  )

  /** 退出练习并从头重看：通知课件恢复画面、回到第一页，前端同步复位状态 */
  const handleRewatch = useCallback(() => {
    const doc = frameRef.current?.contentDocument
    if (!doc) {
      markFailed('html')
      return
    }
    doc.dispatchEvent(new CustomEvent('course:rewatch'))
    setQuizMode(false)
    setPlaying(false)
    syncFromFrame()
  }, [markFailed, syncFromFrame])

  function toggleVideo() {
    const video = videoRef.current
    if (!video) return
    if (video.paused) {
      void video.play().catch((err: unknown) => console.error('视频播放失败', err))
    } else {
      video.pause()
    }
  }

  const [cur, total] = pageLabel.split('/').map((part) => parseInt(part.trim(), 10))
  const atFirst = Number.isFinite(cur) ? cur <= 1 : true
  const atLast = Number.isFinite(cur) && Number.isFinite(total) ? cur >= total : false
  const pageTotal = usablePages.length
  const showLoadMask = source.kind === 'html' && !frameReady
  const allFailed = source.kind === 'none' && failed.length > 0

  return (
    <div className="lp">
      <header className="lp-top">
        <button className="lp-back" type="button" onClick={onBack} aria-label="返回主页面">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M19 12H5m0 0 6-6m-6 6 6 6"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div className="lp-heading">
          <span className="lp-kicker">课件播放</span>
          <h1 className="lp-title">{topic || '课堂讲解'}</h1>
        </div>
      </header>

      <main className="lp-stage">
        <div className="lp-frame">
          {source.kind === 'html' && (
            <iframe
              ref={frameRef}
              className="lp-iframe"
              src={source.src}
              title="课件内容"
              onLoad={handleFrameLoad}
            />
          )}

          {source.kind === 'video' && (
            <video
              ref={videoRef}
              className="lp-video"
              src={source.src}
              playsInline
              onLoadedMetadata={(event) => {
                setVideoDuration(event.currentTarget.duration)
                setVideoTime(0)
              }}
              onTimeUpdate={(event) => setVideoTime(event.currentTarget.currentTime)}
              onPlay={() => setVideoPlaying(true)}
              onPause={() => setVideoPlaying(false)}
              onEnded={() => setVideoPlaying(false)}
              onClick={toggleVideo}
              onError={() => markFailed('video')}
            />
          )}

          {source.kind === 'pages' && (
            <iframe
              key={pageIndex}
              className="lp-iframe"
              srcDoc={usablePages[pageIndex]?.htmlpage ?? ''}
              title={`课件第 ${pageIndex + 1} 页`}
            />
          )}

          {source.kind === 'none' && (
            <div className={`lp-empty${allFailed ? ' is-error' : ''}`}>
              <p className="lp-empty-title">
                {allFailed ? '课件加载失败' : '本次没有生成课件'}
              </p>
              <p className="lp-empty-text">
                {allFailed ? '网络或文件服务异常，可点击重试' : reply || '换个说法再试一次吧'}
              </p>
              {allFailed && (
                <button className="lp-retry" type="button" onClick={() => setFailed([])}>
                  重新加载
                </button>
              )}
            </div>
          )}

          {showLoadMask && (
            <div className="lp-loading" aria-live="polite">
              <span className="lp-spinner" aria-hidden="true" />
              <span>课件加载中…</span>
            </div>
          )}
        </div>

        {failed.length > 0 && source.kind !== 'none' && (
          <p className="lp-fallback-note">
            课件加载异常，已切换到备用内容（{source.kind === 'video' ? '视频' : '逐页展示'}）
          </p>
        )}
      </main>

      <footer className="lp-bar">
        {quizMode ? (
          <button className="lp-ctrl is-primary" type="button" onClick={handleRewatch}>
            重新观看
          </button>
        ) : (
          <LectureControls
            kind={source.kind}
            pageLabel={pageLabel}
            playing={playing}
            atFirst={atFirst}
            atLast={atLast}
            pageIndex={pageIndex}
            pageTotal={pageTotal}
            videoTime={videoTime}
            videoDuration={videoDuration}
            videoPlaying={videoPlaying}
            onPrev={() => clickInside('prev')}
            onToggle={() => (source.kind === 'video' ? toggleVideo() : clickInside('start'))}
            onNext={() => clickInside('next')}
            onPageIndexChange={setPageIndex}
            onSeek={(time) => {
              const video = videoRef.current
              if (!video) return
              video.currentTime = time
              setVideoTime(time)
            }}
          />
        )}
      </footer>
    </div>
  )
}
