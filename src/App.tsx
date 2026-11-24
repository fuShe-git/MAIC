import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelGeneration, chatStream, confirmOutline, retryFailedPages } from './api/chat'
import type { ChatResponse, OutlineModule, ProgressEvent, ProgressStep } from './api/chat'
import { getCourse } from './api/courses'
import { generatedHtmlUrl, listGeneratedFiles } from './api/generated'
import type { GeneratedCourse } from './api/generated'
import { listLlmConfigs } from './api/llmConfigs'
import type { LlmConfig } from './api/llmConfigs'
import { toPageItems } from './utils/courseContext'
import ConfirmView from './components/ConfirmView'
import LecturePlayer from './components/LecturePlayer'
import GeneratingView from './components/GeneratingView'
import type { WaitStatus } from './components/GeneratingView'
import HistorySection from './components/HistorySection'
import SettingsModal from './components/SettingsModal'
import TopBar from './components/TopBar'
import { useI18n } from './i18n/context'
import { EXAMPLES } from './i18n/messages'
import type { MessageKey } from './i18n/messages'
import './App.css'

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

const DEFAULT_PROVIDER_ID = 'platform-default'
const DEFAULT_MODEL_ID = 'platform-default'
const MODEL_STORAGE_KEY = 'maic.composer.model'

interface ModelOption {
  id: string
  name: string
  providerId: string
}

interface ProviderOption {
  id: string
  name: string
  models: ModelOption[]
}

/** 左侧服务商 → 右侧可用模型；服务商来自数据库中已保存的配置 */
function buildModelGroups(
  t: (key: MessageKey) => string,
  configs: LlmConfig[],
): ProviderOption[] {
  const groups: ProviderOption[] = [
    {
      id: DEFAULT_PROVIDER_ID,
      name: t('composer.model.platform'),
      models: [
        {
          id: DEFAULT_MODEL_ID,
          name: t('composer.model.followServer'),
          providerId: DEFAULT_PROVIDER_ID,
        },
      ],
    },
  ]
  configs.forEach((config) => {
    const models = config.models
      .map((model) => ({
        id: `${config.id}:${model.id}`,
        name: model.name.trim(),
        providerId: config.id,
      }))
      .filter((model) => model.name.length > 0)
    groups.push({
      id: config.id,
      name: config.name.trim() || t('composer.model.custom'),
      models,
    })
  })
  return groups
}

function App() {
  const { t, lang } = useI18n()
  const [prompt, setPrompt] = useState('')
  // 设置弹窗开关（首页右上角齿轮入口）
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 视图四态：home 首页 → waiting 等待页 → confirm 确认大纲 → player 课件页
  const [view, setView] = useState<'home' | 'waiting' | 'confirm' | 'player'>('home')
  const [busy, setBusy] = useState(false)
  const [wait, setWait] = useState<WaitState>(INITIAL_WAIT)
  const [lecture, setLecture] = useState<ChatResponse | null>(null)
  // 会话 id：每次生成固定一个，失败页重试靠它定位产物快照
  const [sessionId] = useState(() => `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`)
  // 等待人工确认的大纲 + 图线程 id（confirm 视图数据源）
  const [pendingOutline, setPendingOutline] = useState<OutlineModule[]>([])
  const [threadId, setThreadId] = useState('')
  // 当前进行中的流；返回首页/中断时用它 abort（中断请求）
  const abortRef = useRef<AbortController | null>(null)
  // 当前生成任务 id：中断时用它通知后端真正取消后台任务
  const runIdRef = useRef('')
  // 讲解主题暂存：handleEvent 闭包里的 wait 会过期，done 事件取 topic 用 ref
  const topicRef = useRef('')
  // 本轮流是否已收到终态事件（done/reply/error/confirm/retry-done）：断流兜底判据
  const settledRef = useRef(false)
  // 首页生成记录：videos 目录下的全部课件
  const [generated, setGenerated] = useState<GeneratedCourse[]>([])
  const [generatedLoaded, setGeneratedLoaded] = useState(false)
  // 输入框左下角的模型选择：服务商 / 模型来自后端数据库
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([])
  // 是否已完成一次配置拉取：未完成前不要用「系统默认」覆盖用户记住的模型
  const [llmConfigsLoaded, setLlmConfigsLoaded] = useState(false)
  const [modelGroups, setModelGroups] = useState<ProviderOption[]>(() => buildModelGroups(t, []))
  const [modelId, setModelId] = useState(() => {
    try {
      return localStorage.getItem(MODEL_STORAGE_KEY) || DEFAULT_MODEL_ID
    } catch {
      return DEFAULT_MODEL_ID
    }
  })
  const [activeProviderId, setActiveProviderId] = useState(DEFAULT_PROVIDER_ID)
  const [providerSearch, setProviderSearch] = useState('')
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const modelRootRef = useRef<HTMLDivElement | null>(null)

  /** 拉取数据库里的服务商 / 模型配置；后端不可用时保留「系统默认」兜底项 */
  const refreshLlmConfigs = useCallback(() => {
    listLlmConfigs()
      .then(setLlmConfigs)
      .catch(() => setLlmConfigs([]))
      .finally(() => setLlmConfigsLoaded(true))
  }, [])

  // 首次进入拉取配置；设置页保存成功后也会调用 refreshLlmConfigs 增量刷新
  useEffect(() => {
    refreshLlmConfigs()
  }, [refreshLlmConfigs])

  // 语种切换或配置变更后重建服务商 / 模型分组
  useEffect(() => {
    setModelGroups(buildModelGroups(t, llmConfigs))
  }, [t, llmConfigs])

  // 配置加载完成或被删除后，若记住的模型已不存在则回退「系统默认」
  useEffect(() => {
    if (!llmConfigsLoaded) return
    const allModels = modelGroups.flatMap((group) => group.models)
    if (allModels.length > 0 && !allModels.some((model) => model.id === modelId)) {
      setModelId(allModels[0].id)
    }
  }, [llmConfigsLoaded, modelGroups, modelId])

  // 记住用户选择的模型
  useEffect(() => {
    try {
      localStorage.setItem(MODEL_STORAGE_KEY, modelId)
    } catch {
      // 隐私模式下忽略
    }
  }, [modelId])

  // 点击外部或按 Esc 收起模型菜单
  useEffect(() => {
    if (!modelMenuOpen) return
    const onPointerDown = (event: MouseEvent) => {
      if (modelRootRef.current && !modelRootRef.current.contains(event.target as Node)) setModelMenuOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setModelMenuOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [modelMenuOpen])

  // 进入首页时拉取生成记录；后端不可用时静默降级为空列表
  useEffect(() => {
    if (view !== 'home') return
    const controller = new AbortController()
    listGeneratedFiles(controller.signal)
      .then((data) => setGenerated(data.items ?? []))
      .catch(() => setGenerated([]))
      .finally(() => setGeneratedLoaded(true))
    return () => controller.abort()
  }, [view])

  // 播放页缺少讲解正文时，按 courseId 从课件库补齐，供首次讨论携带整份课件
  useEffect(() => {
    if (view !== 'player' || !lecture?.courseId || (lecture.pageList?.length ?? 0) > 0) return
    const controller = new AbortController()
    const targetId = lecture.courseId
    getCourse(targetId, controller.signal)
      .then((course) => {
        const pages = toPageItems(course.pageList)
        if (pages.length === 0) return
        setLecture((prev) =>
          prev && prev.courseId === targetId && (prev.pageList?.length ?? 0) === 0
            ? { ...prev, topic: prev.topic || course.topic, pageList: pages }
            : prev,
        )
      })
      .catch(() => {
        // 课件库不可用时仍可讨论：后端会按 courseId 自行补齐正文
      })
    return () => controller.abort()
  }, [view, lecture?.courseId, lecture?.pageList])

  // 刷新/关闭页面：通知后端取消后台生成任务（keepalive 尽力保证请求送达）
  useEffect(() => {
    const notifyCancel = () => {
      const runId = runIdRef.current
      if (runId) cancelGeneration(runId, true).catch(() => {})
    }
    window.addEventListener('pagehide', notifyCancel)
    return () => window.removeEventListener('pagehide', notifyCancel)
  }, [])

  /** 把单条 SSE 进度事件映射到等待页状态 */
  function handleEvent(event: ProgressEvent) {
    if (event.step === 'confirm' && event.catalogList) {
      // 大纲产出后停在 interrupt：进入人工确认视图（编辑后继续，或取消）
      settledRef.current = true
      setPendingOutline(event.catalogList)
      setThreadId(event.threadId ?? '')
      setView('confirm')
      return
    }
    if (event.step === 'confirm') {
      // 取消收尾的 confirm/done 通知（无大纲），无需处理
      return
    }
    if (event.step === 'retry') {
      // 手动重试失败页的进度与终态（done 带刷新后的产物与失败清单）
      if (event.status === 'done') {
        settledRef.current = true
        setLecture((prev) =>
          prev
            ? {
                ...prev,
                courseHtml: event.courseHtml ?? prev.courseHtml,
                partial: event.partial ?? prev.partial ?? false,
                failedPages: event.failedPages ?? prev.failedPages ?? [],
              }
            : prev,
        )
        setWait((prev) => ({ ...prev, status: 'ready', step: 'assemble', message: event.message ?? '' }))
      } else {
        setWait((prev) => ({ ...prev, status: 'retrying', step: 'retry', message: event.message ?? '' }))
      }
      return
    }
    if (event.step === 'done') {
      // 课件就绪：数据存起来，但停留等待页，等用户点「开始学习」
      settledRef.current = true
      setLecture((prev) => ({
        reply: prev?.reply ?? '',
        needLecture: true,
        topic: topicRef.current,
        courseHtml: event.courseHtml ?? prev?.courseHtml ?? '',
        videoUrl: event.videoUrl ?? prev?.videoUrl ?? '',
        pageList: prev?.pageList ?? [],
        partial: event.partial ?? prev?.partial ?? false,
        failedPages: event.failedPages ?? prev?.failedPages ?? [],
        threadId: event.threadId ?? prev?.threadId ?? '',
        courseId: event.courseId ?? prev?.courseId ?? '',
      }))
      setWait((prev) => ({ ...prev, status: 'ready', step: 'assemble', message: '' }))
      return
    }
    if (event.step === 'reply') {
      // 闲聊：卡片内直接展示回复文本
      settledRef.current = true
      setWait((prev) => ({ ...prev, status: 'reply', step: null, message: event.message ?? '' }))
      return
    }
    if (event.step === 'error') {
      settledRef.current = true
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
    if (event.step === 'topic' && event.topic) topicRef.current = event.topic
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
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    abortRef.current = controller
    runIdRef.current = runId
    setBusy(true)
    setLecture(null)
    setPendingOutline([])
    setThreadId('')
    topicRef.current = ''
    settledRef.current = false
    setWait(INITIAL_WAIT)
    setView('waiting')

    // modelId 为「系统默认」时后端会回退 .env 的默认模型
    chatStream({ message: text, sessionId, runId, modelId }, handleEvent, controller.signal)
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
        // 流正常结束但始终没等到终态事件（异常断流）：兜底按失败展示，避免一直卡在进行中；
        // confirm 等终态事件会切走视图且 settledRef 已置位，不误判为断流
        if (controller.signal.aborted || settledRef.current) return
        setWait((prev) =>
          prev.status === 'running' || prev.status === 'retrying'
            ? { ...prev, status: 'failed', message: '连接已断开，请重新生成' }
            : prev,
        )
      })
      .finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null
          runIdRef.current = ''
          setBusy(false)
        }
      })
  }

  function handleSubmit() {
    startGeneration(prompt.trim())
  }

  /**
   * 统一中断：断开前端 SSE 连接 + 通知后端 cancel 后台任务。
   * 返回当前 runId（供确认页等复用）；中断后 ref 立即清空，避免重复通知。
   */
  function cancelActiveRun(): string {
    const runId = runIdRef.current
    abortRef.current?.abort()
    abortRef.current = null
    runIdRef.current = ''
    if (runId) {
      // 通知后端真正停止跑图；刷新/卸载场景用 keepalive 提高送达率
      cancelGeneration(runId, true).catch(() => {
        // 取消失败不影响本地中断（服务端 SSE finally 也会兜底 cancel）
      })
    }
    return runId
  }

  /** 等待页「返回首页 / 返回」：中断前后端并复位 */
  function handleBackToHome() {
    cancelActiveRun()
    setBusy(false)
    setLecture(null)
    setWait(INITIAL_WAIT)
    setView('home')
  }

  /** 等待页「中断生成」：中断前后端，停留在等待页展示「已中断」 */
  function handleInterruptGeneration() {
    cancelActiveRun()
    setBusy(false)
    setWait((prev) => ({ ...prev, status: 'interrupted', message: '你已中断本次生成' }))
  }

  /** 等待页「重新生成」：用同一段输入重新发起 */
  function handleRetry() {
    startGeneration(prompt.trim())
  }

  /** 确认视图「确认并生成」：携带编辑后的大纲恢复图执行，重新进等待页 */
  function handleConfirmOutline(catalog: OutlineModule[]) {
    if (!threadId || busy) return
    const controller = new AbortController()
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    abortRef.current = controller
    runIdRef.current = runId
    setBusy(true)
    setPendingOutline([])
    settledRef.current = false
    setWait(INITIAL_WAIT)
    setView('waiting')

    confirmOutline(
      { threadId, catalogList: catalog, confirmed: true, runId, modelId },
      handleEvent,
      controller.signal,
    )
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setWait((prev) => ({
          ...prev,
          status: 'failed',
          message: err instanceof Error ? err.message : '网络异常，请稍后重试',
        }))
      })
      .then(() => {
        if (controller.signal.aborted || settledRef.current) return
        setWait((prev) =>
          prev.status === 'running' || prev.status === 'retrying'
            ? { ...prev, status: 'failed', message: '连接已断开，请重新生成' }
            : prev,
        )
      })
      .finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null
          runIdRef.current = ''
          setBusy(false)
        }
      })
  }

  /** 确认视图「取消」：通知后端取消本次生成，回首页 */
  function handleCancelOutline() {
    if (threadId && !busy) {
      confirmOutline({ threadId, confirmed: false, modelId }, () => {}).catch((err: unknown) => {
        console.error('取消生成失败：', err)
      })
    }
    setPendingOutline([])
    setThreadId('')
    setView('home')
  }

  /** 结果页「重试失败页」：图外直调重跑，进度复用等待页的 retrying 态 */
  function handleRetryFailed() {
    if (busy) return
    const controller = new AbortController()
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    abortRef.current = controller
    runIdRef.current = runId
    setBusy(true)
    settledRef.current = false
    setWait((prev) => ({ ...prev, status: 'retrying', step: 'retry', message: '正在重试失败的页面…' }))

    retryFailedPages({ sessionId, runId, modelId }, handleEvent, controller.signal)
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setWait((prev) => ({
          ...prev,
          status: 'failed',
          message: err instanceof Error ? err.message : '网络异常，请稍后重试',
        }))
      })
      .then(() => {
        if (controller.signal.aborted || settledRef.current) return
        setWait((prev) =>
          prev.status === 'retrying'
            ? { ...prev, status: 'failed', message: '连接已断开，请重试' }
            : prev,
        )
      })
      .finally(() => {
        if (abortRef.current === controller) {
          abortRef.current = null
          runIdRef.current = ''
          setBusy(false)
        }
      })
  }

  /** 等待页「开始学习」：进入课件播放页 */
  function handleStart() {
    if (!lecture) return
    setView('player')
  }

  /** 打开生成记录里的课件：拼同源 HTML 地址，走与「开始学习」相同的播放器视图 */
  function handleOpenGenerated(course: GeneratedCourse) {
    setLecture({
      reply: '',
      needLecture: true,
      // 用户改过备注名时，播放器标题与卡片保持一致
      topic: course.remark || course.topic,
      courseHtml: generatedHtmlUrl(course.fileName),
      videoUrl: '',
      pageList: [],
      courseId: course.id,
    })
    setView('player')
  }

  /** 「我的课堂」删除课件成功后，把该条从首页列表里摘掉 */
  function handleRemoveGenerated(courseId: string) {
    setGenerated((prev) => prev.filter((item) => item.id !== courseId))
  }

  /** 「我的课堂」改名成功后，同步列表里的备注名（接口已落库） */
  function handleRenameGenerated(courseId: string, remark: string) {
    setGenerated((prev) =>
      prev.map((item) => (item.id === courseId ? { ...item, remark } : item)),
    )
  }

  /** 播放页「返回」：回首页 */
  function handleBackFromPlayer() {
    setLecture(null)
    setWait(INITIAL_WAIT)
    setView('home')
  }

  // 输入框占位：提示语 + 当前语种的示例
  const placeholder = `${t('composer.placeholderPrefix')}\n${EXAMPLES[lang]
    .map((item) => `[${item}]`)
    .join('\n')}`

  const allModels = modelGroups.flatMap((group) => group.models)
  const selectedModel = allModels.find((model) => model.id === modelId) ?? allModels[0]
  const selectedProvider = modelGroups.find((group) => group.id === selectedModel.providerId) ?? modelGroups[0]
  const visibleProviders = modelGroups.filter((group) =>
    group.name.toLowerCase().includes(providerSearch.trim().toLowerCase()),
  )
  const activeProvider = visibleProviders.find((group) => group.id === activeProviderId) ?? visibleProviders[0]

  const toggleModelMenu = () => {
    const groups = buildModelGroups(t, llmConfigs)
    setModelGroups(groups)
    const models = groups.flatMap((group) => group.models)
    const current = models.find((model) => model.id === modelId) ?? models[0]
    if (current && current.id !== modelId) setModelId(current.id)
    setActiveProviderId(current?.providerId ?? DEFAULT_PROVIDER_ID)
    setProviderSearch('')
    setModelMenuOpen((prev) => !prev)
  }

  if (view === 'confirm') {
    return (
      <ConfirmView
        catalogList={pendingOutline}
        onConfirm={handleConfirmOutline}
        onCancel={handleCancelOutline}
        busy={busy}
      />
    )
  }

  if (view === 'player' && lecture) {
    return (
      <LecturePlayer
        topic={lecture.topic}
        courseId={lecture.courseId ?? ''}
        pageList={lecture.pageList ?? []}
        courseHtml={lecture.courseHtml}
        videoUrl={lecture.videoUrl}
        reply={lecture.reply}
        modelId={modelId}
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
        onInterrupt={handleInterruptGeneration}
        onRetry={handleRetry}
        onStart={handleStart}
        onRetryFailed={handleRetryFailed}
      />
    )
  }

  return (
    <div className="page">
      <TopBar onOpenSettings={() => setSettingsOpen(true)} />

      <header className="brand">
        <div className="brand-row">
          <img className="brand-icon" src="/img/bitbug_favicon.ico" alt="MAIC" />
          <h1 className="brand-name">MAIC</h1>
        </div>
      </header>

      <main className="composer">
        <div className="composer-head">
          <span className="composer-tip">{t('composer.ready')}</span>
        </div>

        <label className="sr-only" htmlFor="composer-input">
          {t('composer.placeholderPrefix')}
        </label>
        <textarea
          id="composer-input"
          className="composer-input"
          value={prompt}
          placeholder={placeholder}
          onChange={(event) => setPrompt(event.target.value)}
        />

        <div className="composer-foot">
          <div className="composer-model" ref={modelRootRef}>
            <button
              className={`composer-model-trigger ${modelMenuOpen ? 'is-open' : ''}`}
              type="button"
              aria-label={t('composer.model.select')}
              aria-haspopup="menu"
              aria-expanded={modelMenuOpen}
              onClick={toggleModelMenu}
            >
              <svg className="composer-model-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M7.5 17.5h9a4 4 0 0 0 .6-7.96A5.5 5.5 0 0 0 6.9 8.6 4.5 4.5 0 0 0 7.5 17.5Z"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                />
              </svg>
              <span className="composer-model-label">
                {selectedProvider.name} / {selectedModel.name}
              </span>
              <svg className="composer-model-chevron" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>

            {modelMenuOpen && (
              <div className="composer-model-menu" role="menu" aria-label={t('composer.model.select')}>
                <div className="composer-model-search">
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle cx="10.5" cy="10.5" r="6.2" stroke="currentColor" strokeWidth="1.7" />
                    <path d="m15.2 15.2 4.3 4.3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                  </svg>
                  <input
                    value={providerSearch}
                    placeholder={t('composer.model.search')}
                    aria-label={t('composer.model.search')}
                    onChange={(event) => setProviderSearch(event.target.value)}
                  />
                </div>

                <div className="composer-model-columns">
                  <div className="composer-model-providers" role="listbox" aria-label={t('composer.model.search')}>
                    {visibleProviders.length === 0 && (
                      <div className="composer-model-empty">{t('composer.model.noProvider')}</div>
                    )}
                    {visibleProviders.map((group) => (
                      <button
                        key={group.id}
                        className={`composer-model-provider ${group.id === activeProvider?.id ? 'is-active' : ''}`}
                        type="button"
                        role="option"
                        aria-selected={group.id === activeProvider?.id}
                        onClick={() => setActiveProviderId(group.id)}
                      >
                        <svg className="composer-model-provider-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                          <rect x="4" y="7" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.6" />
                          <path d="M9 7V5.5h6V7M8 12h1m3 0h1m3 0h1M9 16h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                        </svg>
                        <span>{group.name}</span>
                      </button>
                    ))}
                  </div>

                  <div className="composer-model-models" role="listbox" aria-label={t('composer.model.select')}>
                    {(!activeProvider || activeProvider.models.length === 0) && (
                      <div className="composer-model-empty">{t('composer.model.noModel')}</div>
                    )}
                    {activeProvider?.models.map((model) => (
                      <button
                        key={model.id}
                        className={`composer-model-option ${model.id === modelId ? 'is-active' : ''}`}
                        type="button"
                        role="option"
                        aria-selected={model.id === modelId}
                        onClick={() => {
                          setModelId(model.id)
                          setModelMenuOpen(false)
                        }}
                      >
                        <span className={`composer-model-radio ${model.id === modelId ? 'is-checked' : ''}`} aria-hidden="true" />
                        <span className="composer-model-name">{model.name}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

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
            <span>{busy ? t('composer.generating') : t('composer.enter')}</span>
          </button>
        </div>
      </main>

      <HistorySection
        items={generated}
        loaded={generatedLoaded}
        onOpen={handleOpenGenerated}
        onRemove={handleRemoveGenerated}
        onRename={handleRenameGenerated}
      />

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onLlmConfigsChanged={refreshLlmConfigs}
      />
    </div>
  )
}

export default App
