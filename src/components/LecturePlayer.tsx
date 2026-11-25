import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { PageItem } from '../api/chat'
import { buildOutlineItems, parsePageNumber, parsePageTotal } from '../utils/courseContext'
import { goToCoursePage } from '../utils/coursewareControl'
import { pickCourseSource } from '../utils/courseSource'
import type { CourseSourceKind } from '../utils/courseSource'
import { useAutoHideControls } from '../hooks/useAutoHideControls'
import { useFullscreen, usePlaybackSettings } from '../hooks/usePlaybackSettings'
import DiscussionPanel from './DiscussionPanel'
import LectureControls from './LectureControls'
import type { LectureControlsProps } from './LectureControls'
import LectureOutline from './LectureOutline'
import LectureTranscript from './LectureTranscript'
import './LecturePlayer.css'

export interface LecturePlayerProps {
  /** 识别出的讲解主题，显示在左上角标题 */
  topic: string
  /** 课件资产 id，讨论时供后端补齐正文 */
  courseId: string
  /** 每个模块的页面 HTML 与讲解正文，courseHtml 缺失时逐页兜底 */
  pageList: PageItem[]
  /** 组装后的课件 HTML 落盘路径，优先展示 */
  courseHtml: string
  /** 导出的 MP4 落盘路径，课件缺失时兜底 */
  videoUrl: string
  /** 后端回复文本，产物全空时兜底展示 */
  reply: string
  /** 当前选中的模型「配置id:模型id」，讨论提问沿用同一模型 */
  modelId?: string
  /** 返回主页面 */
  onBack: () => void
}

/** 课件 HTML 体积大（1~2MB 含 base64 音频），超过该时长仍未加载视为失败并降级 */
const LOAD_TIMEOUT_MS = 15_000
/** 轮询课件内部页码 / 播放态的间隔 */
const POLL_MS = 400
/** 讲解词面板默认高度 */
const LOWER_DEFAULT_HEIGHT = 240
/**
 * 课件画布基准尺寸（16:9）。课件舞台为 min(94vw, 1100px)，
 * 用 1170×658 作 iframe 视口时舞台刚好取到 1100 的原始版式。
 */
const CANVAS_WIDTH = 1170
const CANVAS_HEIGHT = 658
/** 左侧大纲默认宽度，拖拽范围由 LectureOutline 限制 */
const OUTLINE_DEFAULT_WIDTH = 208

export default function LecturePlayer({
  topic,
  courseId,
  pageList,
  courseHtml,
  videoUrl,
  reply,
  modelId,
  onBack,
}: LecturePlayerProps) {
  /** 加载失败过的展示类型，pickCourseSource 会跳过它们逐级降级 */
  const [failed, setFailed] = useState<CourseSourceKind[]>([])
  const [frameReady, setFrameReady] = useState(false)
  const [pageLabel, setPageLabel] = useState('1 / 1')
  /** 讨论区展开状态；窄屏时表现为覆盖抽屉 */
  const [discussionOpen, setDiscussionOpen] = useState(false)
  const [playing, setPlaying] = useState(false)
  /** 课件已进入课后练习：底部控制区切换为「重新观看」 */
  const [quizMode, setQuizMode] = useState(false)
  /** 逐页兜底模式下的当前页下标 */
  const [pageIndex, setPageIndex] = useState(0)
  const [videoTime, setVideoTime] = useState(0)
  const [videoDuration, setVideoDuration] = useState(0)
  const [videoPlaying, setVideoPlaying] = useState(false)
  const [lowerCollapsed, setLowerCollapsed] = useState(false)
  const [lowerHeight, setLowerHeight] = useState(LOWER_DEFAULT_HEIGHT)
  const [discussionWidth, setDiscussionWidth] = useState(300)
  /** 大纲默认展开；窄屏默认收起，避免一进页面就遮住课件 */
  const [outlineOpen, setOutlineOpen] = useState(
    () => typeof window === 'undefined' || window.innerWidth > 900,
  )
  /** 左侧大纲宽度，拖动右边缘时更新 */
  const [outlineWidth, setOutlineWidth] = useState(OUTLINE_DEFAULT_WIDTH)

  // 展开讨论时锁住页面滚动，只保留消息列表内部滚动，避免整页出现滚动条
  useEffect(() => {
    document.documentElement.classList.toggle('discussion-open', discussionOpen)
    document.body.classList.toggle('discussion-open', discussionOpen)
    return () => {
      document.documentElement.classList.remove('discussion-open')
      document.body.classList.remove('discussion-open')
    }
  }, [discussionOpen])

  const rootRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const frameWrapRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const lastPageRef = useRef('')
  const manualNavRef = useRef(false)
  const [canvasScale, setCanvasScale] = useState(1)

  // 讲解词面板高度由 DOM 变量驱动：拖动过程中直接修改，避免 React 重渲染覆盖临时高度；
  // 这里只在折叠状态切换时同步，拖动提交的高度已由面板自己写回 DOM
  const lowerHeightRef = useRef(lowerHeight)
  lowerHeightRef.current = lowerHeight

  useLayoutEffect(() => {
    const root = rootRef.current
    if (!root) return
    root.style.setProperty('--lp-lower-h', `${lowerCollapsed ? 0 : lowerHeightRef.current}px`)
  }, [lowerCollapsed])

  /**
   * 课件舞台是 `width: min(94vw, 1100px)` 的 16:9 版式，页内排版（字号、`100vh`
   * 宽度计算）都基于这个视口。前端容器通常比它窄，若直接把 iframe 缩成容器大小，
   * 课件会按窄视口重排 → 内容挤压、溢出、比例错乱。
   * 因此给 iframe 一个固定画布（1170×658 ≈ 1100/0.94 的 16:9），等价于「在大窗口里
   * 直接打开课件」，再把整块画布等比缩放到容器；全屏时由 CSS 还原为铺满。
   * 全程不进课件内部改布局，页内样式保持原样。
   */
  useLayoutEffect(() => {
    const wrap = frameWrapRef.current
    if (!wrap) return
    const measure = () => {
      const { width, height } = wrap.getBoundingClientRect()
      if (!width || !height) return
      setCanvasScale(Math.min(width / CANVAS_WIDTH, height / CANVAS_HEIGHT))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(wrap)
    return () => observer.disconnect()
  }, [])

  const source = useMemo(
    () => pickCourseSource({ courseHtml, videoUrl, pageList }, failed),
    [courseHtml, videoUrl, pageList, failed],
  )

  const {
    autoPlay,
    toggleAutoPlay,
    rate,
    setRate,
    volume,
    setVolume,
    apiAvailableRef,
  } = usePlaybackSettings(frameRef, videoRef, frameReady, source.kind)
  const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(frameWrapRef)
  // 全屏浮层控制条：静止一段时间自动隐藏，鼠标移动/按键/触摸重新出现
  const fsControls = useAutoHideControls(isFullscreen)

  // 课件 body 是从顶部起排的，全屏放大后舞台会贴顶偏上；这里让它垂直居中
  useEffect(() => {
    const body = frameRef.current?.contentDocument?.body
    if (!body) return
    body.style.justifyContent = isFullscreen ? 'center' : ''
  }, [isFullscreen, frameReady])

  /**
   * 课件（及逐页兜底）都装在 iframe 里，指针进入 iframe 后 mousemove 只在子文档触发、
   * 不会冒泡到父文档，所以父文档和同源子文档都要监听；跨域拿不到文档时只监听父文档。
   */
  useEffect(() => {
    if (!isFullscreen) return
    const { poke } = fsControls
    const frameDocs = Array.from(frameWrapRef.current?.querySelectorAll('iframe') ?? [])
      .map((frame) => frame.contentDocument)
      .filter((doc): doc is Document => Boolean(doc))
    const targets: Document[] = [document, ...frameDocs]
    targets.forEach((doc) => {
      doc.addEventListener('mousemove', poke)
      doc.addEventListener('pointerdown', poke)
    })
    document.addEventListener('keydown', poke)
    return () => {
      targets.forEach((doc) => {
        doc.removeEventListener('mousemove', poke)
        doc.removeEventListener('pointerdown', poke)
      })
      document.removeEventListener('keydown', poke)
    }
  }, [isFullscreen, fsControls.poke, frameReady, source.kind, source.src, pageIndex])

  const usablePages = useMemo(
    () => pageList.filter((page) => page.htmlpage.trim()),
    [pageList],
  )
  const outlineItems = useMemo(() => buildOutlineItems(pageList), [pageList])

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
    lastPageRef.current = ''
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
    if (num) {
      // 旧课件无控制接口：自动播放关闭时把未经手动触发的翻页回退
      if (
        !autoPlay &&
        !apiAvailableRef.current &&
        !manualNavRef.current &&
        lastPageRef.current &&
        num !== lastPageRef.current
      ) {
        manualNavRef.current = true
        doc.getElementById('prev')?.click()
        const start = doc.getElementById('start')
        if (start?.textContent?.trim() === '暂停') start.click()
        window.setTimeout(() => {
          manualNavRef.current = false
        }, 0)
      }
      lastPageRef.current = num
      setPageLabel(num)
    }
    const start = doc.getElementById('start')?.textContent?.trim()
    if (start) setPlaying(start === '暂停')
    // 课件脚本在进入练习时给 body 打标记，前端据此切换底部控制区
    setQuizMode(doc.body.classList.contains('quiz-mode'))
  }, [autoPlay, apiAvailableRef])

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
      manualNavRef.current = true
      doc.getElementById(id)?.click()
      window.setTimeout(() => {
        syncFromFrame()
        manualNavRef.current = false
      }, 0)
    },
    [markFailed, syncFromFrame],
  )

  /**
   * 大纲跳页：新课件直接调用 goTo；旧课件只能点内部上一页/下一页，
   * 用有界循环补齐距离，并按跳转前的播放状态决定是否恢复。
   */
  const jumpToPage = useCallback(
    (page: number) => {
      if (!Number.isFinite(page) || page < 1) return
      if (source.kind === 'pages') {
        setPageIndex(page - 1)
        return
      }
      if (source.kind !== 'html') return

      const frame = frameRef.current
      const doc = frame?.contentDocument
      if (!doc) {
        markFailed('html')
        return
      }

      const wasPlaying = playing && !quizMode
      manualNavRef.current = true
      const jumped = goToCoursePage(frame, page - 1)

      if (!jumped) {
        const current = parsePageNumber(doc.getElementById('pageNum')?.textContent ?? '') ?? 1
        const delta = page - current
        const buttonId = delta > 0 ? 'next' : 'prev'
        const times = Math.min(Math.abs(delta), outlineItems.length)
        for (let i = 0; i < times; i += 1) doc.getElementById(buttonId)?.click()
        // 旧课件每次 click 都会启动音频：原本暂停时，跳页后补一次暂停
        const start = doc.getElementById('start')
        if (!wasPlaying && start?.textContent?.trim() === '暂停') start.click()
      } else if (wasPlaying) {
        doc.getElementById('start')?.click()
      }

      window.setTimeout(() => {
        syncFromFrame()
        manualNavRef.current = false
      }, 0)
    },
    [
      source.kind,
      playing,
      quizMode,
      outlineItems.length,
      markFailed,
      syncFromFrame,
    ],
  )

  /** 宽屏允许大纲与讨论区同时展开；窄屏两者都是抽屉，打开其一收起另一 */
  const toggleOutline = useCallback(() => {
    if (!outlineOpen && window.innerWidth <= 900) setDiscussionOpen(false)
    setOutlineOpen((value) => !value)
  }, [outlineOpen])

  const toggleDiscussion = useCallback(() => {
    if (!discussionOpen && window.innerWidth <= 900) setOutlineOpen(false)
    setDiscussionOpen((value) => !value)
  }, [discussionOpen])

  /** 讨论输入框聚焦时暂停讲解，避免音频与讨论互相干扰 */
  const pauseCourseware = useCallback(() => {
    if (source.kind === 'video') {
      videoRef.current?.pause()
      return
    }
    if (source.kind === 'html' && playing) {
      clickInside('start')
    }
  }, [source.kind, playing, clickInside])

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

  const cur = parsePageNumber(pageLabel)
  const total = parsePageTotal(pageLabel)
  const atFirst = cur === null ? true : cur <= 1
  const atLast = cur !== null && total !== null ? cur >= total : false
  const pageTotal = usablePages.length
  // 逐页兜底模式下页码由前端控制，讨论区与讲解词也跟着当前页走
  const activePageLabel =
    source.kind === 'pages' ? `${pageIndex + 1} / ${pageTotal || 1}` : pageLabel
  // 视频与空白兜底没有分页概念；单页课件也不展示大纲
  const showOutline =
    outlineItems.length > 1 && (source.kind === 'html' || source.kind === 'pages')
  const outlineVisible = showOutline && outlineOpen
  const outlineCurrentPage = source.kind === 'pages' ? pageIndex + 1 : cur
  const transcript = useMemo(() => {
    const current = parsePageNumber(activePageLabel)
    if (!current) return ''
    const page = pageList.find((item) => Number(item.ind) === current) ?? pageList[current - 1]
    // context 由后端按「标题：口播稿」逐行拼接，下方只展示冒号后的正文
    return (page?.context ?? '')
      .trim()
      .split('\n')
      .map((line) => {
        const colon = line.indexOf('：')
        return colon >= 0 ? line.slice(colon + 1).trim() : line.trim()
      })
      .filter(Boolean)
      .join('\n')
  }, [activePageLabel, pageList])
  const showLoadMask = source.kind === 'html' && !frameReady
  const allFailed = source.kind === 'none' && failed.length > 0

  // 控制条：面板展开时作为讲解词区域的头部，折叠时单独作为底部栏
  const controlProps: LectureControlsProps = {
    kind: source.kind,
    pageLabel: activePageLabel,
    outlineAvailable: showOutline,
    outlineOpen: outlineVisible,
    onToggleOutline: toggleOutline,
    playing,
    atFirst,
    atLast,
    pageIndex,
    pageTotal,
    videoTime,
    videoDuration,
    videoPlaying,
    quizMode,
    onRewatch: handleRewatch,
    discussionOpen,
    onToggleDiscussion: toggleDiscussion,
    autoPlay,
    onToggleAutoPlay: toggleAutoPlay,
    rate,
    onRateChange: setRate,
    volume,
    onVolumeChange: setVolume,
    isFullscreen,
    onToggleFullscreen: toggleFullscreen,
    lowerCollapsed,
    onToggleLower: () => setLowerCollapsed((value) => !value),
    audioEnabled: source.kind === 'html' || source.kind === 'video',
    onPrev: () => clickInside('prev'),
    onToggle: () => (source.kind === 'video' ? toggleVideo() : clickInside('start')),
    onNext: () => clickInside('next'),
    onPageIndexChange: setPageIndex,
    onSeek: (time: number) => {
      const video = videoRef.current
      if (!video) return
      video.currentTime = time
      setVideoTime(time)
    },
  }
  const controls = <LectureControls {...controlProps} />

  return (
    <div
      ref={rootRef}
      className={`lp${outlineVisible ? ' has-outline' : ''}${discussionOpen ? ' has-discussion' : ''}${lowerCollapsed ? ' is-lower-collapsed' : ''}`}
      style={
        {
          '--dp-width': `${discussionWidth}px`,
          '--lp-outline-w': outlineVisible ? `${outlineWidth}px` : '0px',
        } as CSSProperties
      }
    >
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

      {outlineVisible && (
        <LectureOutline
          items={outlineItems}
          currentPage={outlineCurrentPage}
          width={outlineWidth}
          onWidthChange={setOutlineWidth}
          disabled={source.kind === 'html' && !frameReady}
          onSelect={jumpToPage}
          onCollapse={() => setOutlineOpen(false)}
        />
      )}

      <main className={`lp-stage${discussionOpen ? ' has-discussion' : ''}`}>
        <div className="lp-stage-main">
          <div
            className="lp-frame"
            ref={frameWrapRef}
            style={
              {
                '--lp-canvas-w': `${CANVAS_WIDTH}px`,
                '--lp-canvas-h': `${CANVAS_HEIGHT}px`,
                '--lp-canvas-scale': canvasScale,
              } as CSSProperties
            }
          >
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

          {/* 全屏时底部控制条不可见，这里浮出同样的倍速/音量/翻页按钮 */}
          {isFullscreen && (
            <div
              className={`lp-fs-overlay${fsControls.visible ? '' : ' is-hidden'}`}
              onMouseEnter={fsControls.hold}
              onMouseLeave={fsControls.release}
            >
              <LectureControls {...controlProps} variant="overlay" />
            </div>
          )}
        </div>

        {failed.length > 0 && source.kind !== 'none' && (
          <p className="lp-fallback-note">
            课件加载异常，已切换到备用内容（{source.kind === 'video' ? '视频' : '逐页展示'}）
          </p>
        )}
        </div>

        <DiscussionPanel
          topic={topic}
          courseId={courseId}
          pageList={pageList}
          pageLabel={activePageLabel}
          modelId={modelId}
          open={discussionOpen}
          onToggle={() => setDiscussionOpen(false)}
          onFocusInput={pauseCourseware}
          width={discussionWidth}
          onWidthChange={setDiscussionWidth}
        />
      </main>

      <div className="lp-bottom">
        <LectureTranscript
          text={transcript}
          rootRef={rootRef}
          height={lowerHeight}
          onHeightCommit={setLowerHeight}
          onCollapse={() => setLowerCollapsed(true)}
          onExpand={(nextHeight) => {
            setLowerCollapsed(false)
            setLowerHeight(nextHeight)
          }}
          collapsed={lowerCollapsed}
          toolbar={controls}
        />
      </div>
    </div>
  )
}
