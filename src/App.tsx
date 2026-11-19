import { useRef, useState } from 'react'
import { streamMessage } from './api/chat'
import type { ChatResponse, ProgressEvent, ProgressStep } from './api/chat'
import LecturePlayer from './components/LecturePlayer'
import GeneratingView from './components/GeneratingView'
import type { WaitStatus } from './components/GeneratingView'
import './App.css'

const EXAMPLES = [
  '从零学 Python，30 分钟写出第一个程序',
  '用正弦函数讲讲傅里叶变换',
  '阿瓦隆桌游怎么玩',
]

const PLACEHOLDER = `输入你想学的任何内容，例如：
${EXAMPLES.map((item) => `[${item}]`).join('\n')}`

/** 等待页的进度状态快照 */
interface WaitState {
  /** 所处阶段 */
  status: WaitStatus
  /** 当前步骤；null 表示尚未收到进度 */
  step: ProgressStep | null
  /** 页面生成：已完成 / 总数 */
  pagesCurrent: number
  pagesTotal: number
  /** 音频合成：已完成 / 总数 */
  audioCurrent: number
  audioTotal: number
  /** 闲聊回复文本或错误信息 */
  message: string
  /** 讲解主题（topic 步骤展示「讲解主题：xxx」） */
  topic: string
}

const INITIAL_WAIT: WaitState = {
  status: 'running',
  step: null,
  pagesCurrent: 0,
  pagesTotal: 0,
  audioCurrent: 0,
  audioTotal: 0,
  message: '',
  topic: '',
}

function ChevronDown() {
  return (
    <svg
      className="icon icon-xs"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M4 6.5 8 10l4-3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function App() {
  const [prompt, setPrompt] = useState('')
  const [deepMode, setDeepMode] = useState(true)
  // 视图三态：home 首页 → waiting 等待页 → player 课件页
  const [view, setView] = useState<'home' | 'waiting' | 'player'>('home')
  const [busy, setBusy] = useState(false)
  const [wait, setWait] = useState<WaitState>(INITIAL_WAIT)
  const [lecture, setLecture] = useState<ChatResponse | null>(null)
  // 当前进行中的流；返回首页时用它 abort（中断请求）
  const abortRef = useRef<AbortController | null>(null)

  /** 把单条 SSE 进度事件映射到等待页状态 */
  function handleEvent(event: ProgressEvent) {
    if (event.step === 'done' && event.data) {
      // 课件就绪：数据存起来，但停留等待页，等用户点「开始学习」
      setLecture(event.data)
      setWait((prev) => ({ ...prev, status: 'ready', step: 'assemble', message: '' }))
      return
    }
    if (event.step === 'reply') {
      // 闲聊：卡片内直接展示回复文本
      setWait((prev) => ({ ...prev, status: 'reply', step: null, message: event.message ?? '' }))
      return
    }
    if (event.step === 'error') {
      if (event.status === 'retrying') {
        // 后端已重新跑图：进度归零，回到第一个步骤重新推进
        setWait({ ...INITIAL_WAIT, status: 'retrying', message: event.message ?? '' })
      } else {
        // 最终失败：停留本页展示错误，不出现开始学习按钮
        setWait((prev) => ({ ...prev, status: 'failed', message: event.message ?? '生成出错，请重试' }))
      }
      return
    }

    // 过程进度：check / topic / outline / pages / audio / assemble
    // 重试期间停留在 retrying 态（保留「重试中」提示），直到重新走完检查进入大纲阶段
    setWait((prev) => ({
      ...prev,
      status: prev.status === 'retrying' && event.step !== 'check' ? 'running' : prev.status,
      step: event.step,
      // 讲解主题在 topic 步骤事件里带到前端；新一轮生成/重试时已由 INITIAL_WAIT 清空
      topic: event.step === 'topic' ? event.topic ?? prev.topic : prev.topic,
      pagesCurrent: event.step === 'pages' ? event.current ?? prev.pagesCurrent : prev.pagesCurrent,
      pagesTotal:
        event.step === 'pages' || event.step === 'outline' || event.step === 'assemble'
          ? event.total || prev.pagesTotal
          : prev.pagesTotal,
      audioCurrent: event.step === 'audio' ? event.current ?? prev.audioCurrent : prev.audioCurrent,
      audioTotal:
        event.step === 'audio' || event.step === 'outline'
          ? event.total || prev.audioTotal
          : prev.audioTotal,
    }))
  }

  /** 发起一次生成：立即进等待页，流式接收进度 */
  function startGeneration(text: string) {
    if (!text || busy) return
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setLecture(null)
    setWait(INITIAL_WAIT)
    setView('waiting')

    streamMessage(text, handleEvent, controller.signal)
      .catch((err: unknown) => {
        // 用户主动返回首页触发的中断不按错误处理
        if (controller.signal.aborted) return
        setWait((prev) => ({
          ...prev,
          status: 'failed',
          message: err instanceof Error ? err.message : '网络异常，请稍后重试',
        }))
      })
      .then(() => {
        // 流正常结束但始终没等到终态事件（异常断流）：兜底按失败展示，避免一直卡在进行中
        if (controller.signal.aborted) return
        setWait((prev) =>
          prev.status === 'running' || prev.status === 'retrying'
            ? { ...prev, status: 'failed', message: '连接已断开，请重新生成' }
            : prev,
        )
      })
      .finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null
          setBusy(false)
        }
      })
  }

  function handleSubmit() {
    startGeneration(prompt.trim())
  }

  /** 等待页「返回首页 / 返回」：中断流并复位 */
  function handleBackToHome() {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setLecture(null)
    setWait(INITIAL_WAIT)
    setView('home')
  }

  /** 等待页「重新生成」：用同一段输入重新发起 */
  function handleRetry() {
    startGeneration(prompt.trim())
  }

  /** 等待页「开始学习」：进入课件播放页 */
  function handleStart() {
    if (!lecture) return
    setView('player')
  }

  /** 播放页「返回」：回首页 */
  function handleBackFromPlayer() {
    setLecture(null)
    setWait(INITIAL_WAIT)
    setView('home')
  }

  if (view === 'player' && lecture) {
    return (
      <LecturePlayer
        topic={lecture.topic}
        pageList={lecture.pageList}
        courseHtml={lecture.courseHtml}
        videoUrl={lecture.videoUrl}
        reply={lecture.reply}
        onBack={handleBackFromPlayer}
      />
    )
  }

  if (view === 'waiting') {
    return (
      <GeneratingView
        status={wait.status}
        step={wait.step}
        pagesCurrent={wait.pagesCurrent}
        pagesTotal={wait.pagesTotal}
        audioCurrent={wait.audioCurrent}
        audioTotal={wait.audioTotal}
        message={wait.message}
        topic={wait.topic}
        lecture={lecture}
        onBack={handleBackToHome}
        onRetry={handleRetry}
        onStart={handleStart}
      />
    )
  }

  return (
    <div className="page">
      <header className="brand">
        <div className="brand-row">
          <img className="brand-icon" src="/img/bitbug_favicon.ico" alt="MAIC" />
          <h1 className="brand-name">MAIC</h1>
        </div>
      </header>

      <main className="composer">
        <div className="composer-head">
          <div className="user">
            <span className="avatar" aria-hidden="true" />
            <span className="user-name">同学</span>
            <ChevronDown />
          </div>

          <span className="composer-tip">准备好一起学习了吗?</span>

          <button className="model-switch" type="button">
            <svg className="icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" />
              <path
                d="M2 8h12M8 2c1.8 1.7 2.7 3.7 2.7 6S9.8 12.3 6.2 12.3 3.3 10 3.3 8 6.2 3.7 8 2Z"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
            <span>API</span>
            <ChevronDown />
          </button>
        </div>

        <label className="sr-only" htmlFor="composer-input">
          输入你想学的任何内容
        </label>
        <textarea
          id="composer-input"
          className="composer-input"
          value={prompt}
          placeholder={PLACEHOLDER}
          onChange={(event) => setPrompt(event.target.value)}
        />

        <div className="composer-foot">
          <button className="icon-btn" type="button" aria-label="添加附件">
            <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M20 11.5 12.3 19a4.6 4.6 0 0 1-6.5-6.5l7.6-7.6a3 3 0 0 1 4.3 4.3l.6.6"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          <div className="composer-actions">
            <button
              className={`btn-deep${deepMode ? ' is-active' : ''}`}
              type="button"
              onClick={() => setDeepMode((value) => !value)}
            >
              <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 3v3M12 18v3M4.2 7.5l2.6 1.5M17.2 15l2.6 1.5M4.2 16.5l2.6-1.5M17.2 9l2.6-1.5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.6" />
              </svg>
              <span>深度交互</span>
            </button>

            <button
              className="btn-enter"
              type="button"
              disabled={!prompt.trim() || busy}
              onClick={handleSubmit}
            >
              <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 19V5M6 11l6-6 6 6"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span>{busy ? '生成中…' : '进入课堂'}</span>
            </button>
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
