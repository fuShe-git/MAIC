import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cancelGeneration, chatStream, generateCourse, precheckInput, retryFailedPages } from './api/chat'
import type { ChatResponse, OutlineModule, ProgressEvent, ProgressStep } from './api/chat'
import { getCourse } from './api/courses'
import { generatedHtmlUrl, listGeneratedFiles } from './api/generated'
import type { GeneratedCourse } from './api/generated'
import { listLlmConfigs } from './api/llmConfigs'
import type { LlmConfig } from './api/llmConfigs'
import { toPageItems } from './utils/courseContext'
import { FALLBACK_THEMES, generateThemes } from './api/theme'
import type { ThemeTemplate } from './api/theme'
import PrepareWorkspace from './components/PrepareWorkspace'
import type { OutlinePanelStatus, PrepareStep, ThemePanelStatus } from './components/PrepareWorkspace'
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
  // 首页前置校验：违规 / 是否有讲解主题；失败时留在首页展示提示
  // 存 code 而非文案，切换语言时已显示的提示会跟着翻译
  const [precheckBusy, setPrecheckBusy] = useState(false)
  const [precheckError, setPrecheckError] = useState('')
  // 设置弹窗开关（首页右上角齿轮入口）
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 视图四态：home 首页 → prepare 准备台 → generating 生成进度 → player 课件页
  const [view, setView] = useState<'home' | 'prepare' | 'generating' | 'player'>('home')
  // 准备台内部阶段：prepare 停留前两步，generate 进入第三步课件生成
  const [stage, setStage] = useState<'prepare' | 'generate'>('prepare')
  // 准备台当前显示的步骤（受控，切步骤不会触发额外渲染）
  const [prepareStep, setPrepareStep] = useState<PrepareStep>('outline')
  const [wait, setWait] = useState<WaitState>(INITIAL_WAIT)
  const [lecture, setLecture] = useState<ChatResponse | null>(null)
  // 会话 id：每次生成固定一个，失败页重试靠它定位产物快照
  const [sessionId] = useState(() => `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`)
  // 准备工作台：大纲与 PPT 样式各自独立加载、调试
  const [pendingOutline, setPendingOutline] = useState<OutlineModule[]>([])
  const [outlineDraft, setOutlineDraft] = useState<OutlineModule[]>([])
  const [, setThreadId] = useState('')
  const [outlineStatus, setOutlineStatus] = useState<OutlinePanelStatus>('loading')
  const [outlineError, setOutlineError] = useState('')
  const [themes, setThemes] = useState<ThemeTemplate[]>([])
  const [themeStatus, setThemeStatus] = useState<ThemePanelStatus>('loading')
  const [themeError, setThemeError] = useState('')
  const [selectedThemeId, setSelectedThemeId] = useState('')
  const [courseTitle, setCourseTitle] = useState('')
  // 前置校验单独取消；准备阶段两条任务分别取消；生成阶段复用 abortRef/runIdRef
  const precheckAbortRef = useRef<AbortController | null>(null)
  const precheckTokenRef = useRef('')
  const outlineAbortRef = useRef<AbortController | null>(null)
  const themeAbortRef = useRef<AbortController | null>(null)
  const outlineRunIdRef = useRef('')
  const outlineSettledRef = useRef(false)
  const sourceMessageRef = useRef('')
  const streamModeRef = useRef<'outline' | 'course'>('outline')
  const abortRef = useRef<AbortController | null>(null)
  const runIdRef = useRef('')
  const topicRef = useRef('')
  const settledRef = useRef(false)
  const busy =
    precheckBusy ||
    (view === 'prepare' &&
      stage === 'prepare' &&
      (outlineStatus === 'loading' ||
        outlineStatus === 'regenerating' ||
        themeStatus === 'loading' ||
        themeStatus === 'regenerating')) ||
    (stage === 'generate' && (wait.status === 'running' || wait.status === 'retrying'))
  // 首页生成记录：videos 目录下的全部课件
  const [generated, setGenerated] = useState<GeneratedCourse[]>([])
  const [generatedLoaded, setGeneratedLoaded] = useState(false)
  // 输入框左下角的模型选择：服务商 / 模型来自后端数据库
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([])
  // 是否已完成一次配置拉取：未完成前不要用「系统默认」覆盖用户记住的模型
  const [llmConfigsLoaded, setLlmConfigsLoaded] = useState(false)
  // 最近一次拉取是否成功：失败时列表为空是「接口不可用」，不是「用户删光了配置」
  const [llmConfigsAvailable, setLlmConfigsAvailable] = useState(true)
  // 同步派生，避免配置到达与分组重建之间出现「只有系统默认」的中间帧误伤用户选择
  const modelGroups = useMemo(() => buildModelGroups(t, llmConfigs), [t, llmConfigs])
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
      .then((items) => {
        setLlmConfigs(items)
        setLlmConfigsAvailable(true)
      })
      .catch(() => {
        setLlmConfigs([])
        // 接口失败 ≠ 配置被删空：记住的选择必须保留，不能被误判为失效
        setLlmConfigsAvailable(false)
      })
      .finally(() => setLlmConfigsLoaded(true))
  }, [])

  // 首次进入拉取配置；设置页保存成功后也会调用 refreshLlmConfigs 增量刷新
  useEffect(() => {
    refreshLlmConfigs()
  }, [refreshLlmConfigs])

  // 仅在「拉取成功」且记住的模型确实已被删除时回退，并清掉本地覆盖
  // 用渲染期比较代替 effect，避免同步 setState 引发级联渲染
  const availableModels = modelGroups.flatMap((group) => group.models)
  const modelStale =
    llmConfigsLoaded &&
    llmConfigsAvailable &&
    availableModels.length > 0 &&
    !availableModels.some((model) => model.id === modelId)
  const [prevModelStale, setPrevModelStale] = useState(false)
  if (modelStale && !prevModelStale) {
    setPrevModelStale(true)
    setModelId(DEFAULT_MODEL_ID)
    try {
      localStorage.removeItem(MODEL_STORAGE_KEY)
    } catch {
      // 隐私模式下忽略
    }
  } else if (!modelStale && prevModelStale) {
    setPrevModelStale(false)
  }

  /** 只有用户主动选择才写入 localStorage；自动回退 / 配置刷新都不改写偏好 */
  function selectModel(id: string) {
    setModelId(id)
    try {
      if (id === DEFAULT_MODEL_ID) localStorage.removeItem(MODEL_STORAGE_KEY)
      else localStorage.setItem(MODEL_STORAGE_KEY, id)
    } catch {
      // 隐私模式下忽略
    }
  }

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
      const outlineRunId = outlineRunIdRef.current
      precheckAbortRef.current?.abort()
      outlineAbortRef.current?.abort()
      themeAbortRef.current?.abort()
      if (runId) cancelGeneration(runId, true).catch(() => {})
      if (outlineRunId) cancelGeneration(outlineRunId, true).catch(() => {})
    }
    window.addEventListener('pagehide', notifyCancel)
    return () => window.removeEventListener('pagehide', notifyCancel)
  }, [])

  /** 把单条 SSE 进度事件映射到当前流：准备阶段只关心大纲终态，生成阶段关心完整进度 */
  function handleEvent(event: ProgressEvent) {
    if (streamModeRef.current === 'outline') {
      if (event.step === 'confirm' && event.catalogList) {
        outlineSettledRef.current = true
        outlineRunIdRef.current = ''
        setPendingOutline(event.catalogList)
        setOutlineDraft(event.catalogList)
        setThreadId(event.threadId ?? '')
        setOutlineStatus('ready')
        setOutlineError('')
        setView('prepare')
        return
      }
      if (event.step === 'reply' || event.step === 'error' || event.step === 'done') {
        outlineSettledRef.current = true
        outlineRunIdRef.current = ''
        setOutlineStatus('error')
        setOutlineError(event.message || t('precheck.NO_TOPIC'))
      }
      // 准备阶段不展示 check/safety/topic 等过程进度
      return
    }
    if (event.step === 'confirm') {
      // 课件生成流里的 confirm 是收尾通知，无需处理
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

  /** 把前置校验 code 映射成当前语言的文案；未知 code 按「未检测到讲解内容」兜底 */
  function precheckMessage(code: string): string {
    switch (code) {
      case 'SAFETY_BLOCKED':
        return t('precheck.SAFETY_BLOCKED')
      case 'NO_LECTURE':
        return t('precheck.NO_LECTURE')
      case 'PRECHECK_ERROR':
        return t('precheck.PRECHECK_ERROR')
      case 'NO_TOPIC':
      default:
        return t('precheck.NO_TOPIC')
    }
  }

  /** 取消首页前置校验请求 */
  function cancelPrecheckTask() {
    precheckAbortRef.current?.abort()
    precheckAbortRef.current = null
  }

  /** 取消准备阶段的大纲流（含后端任务） */
  function cancelOutlineTask() {
    const runId = outlineRunIdRef.current
    outlineAbortRef.current?.abort()
    outlineAbortRef.current = null
    outlineRunIdRef.current = ''
    if (runId) cancelGeneration(runId, true).catch(() => {})
  }

  /** 取消准备阶段的样式请求 */
  function cancelThemeTask() {
    themeAbortRef.current?.abort()
    themeAbortRef.current = null
  }

  /** 发起/重新发起大纲流：只跑到大纲确认事件即停 */
  function runOutlineStream(
    message: string,
    mode: 'loading' | 'regenerating',
    options?: { topic?: string; outlineHint?: string; precheckToken?: string },
  ) {
    cancelOutlineTask()
    const controller = new AbortController()
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    outlineAbortRef.current = controller
    outlineRunIdRef.current = runId
    outlineSettledRef.current = false
    streamModeRef.current = 'outline'
    setOutlineStatus(mode)
    setOutlineError('')

    // modelId 为「系统默认」时后端会回退 .env 的默认模型
    chatStream(
      {
        message,
        sessionId,
        runId,
        modelId,
        autoConfirm: false,
        outlineOnly: mode === 'regenerating',
        topic: options?.topic,
        outlineHint: options?.outlineHint,
        precheckToken: options?.precheckToken,
      },
      handleEvent,
      controller.signal,
    )
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setOutlineStatus('error')
        setOutlineError(err instanceof Error ? err.message : '大纲生成失败，请调整主题后重试')
      })
      .then(() => {
        if (controller.signal.aborted || outlineSettledRef.current) return
        setOutlineStatus('error')
        setOutlineError('大纲生成连接已断开，请重新生成')
      })
      .finally(() => {
        if (outlineAbortRef.current === controller) outlineAbortRef.current = null
        if (outlineRunIdRef.current === runId) outlineRunIdRef.current = ''
      })
  }

  /** 发起/重新发起 PPT 样式生成；降级为内置模板时按正常流程继续，不阻断课件生成 */
  function runThemeRequest(payload: { message: string; feedback?: string; currentThemes?: ThemeTemplate[] }, mode: 'loading' | 'regenerating') {
    cancelThemeTask()
    const controller = new AbortController()
    themeAbortRef.current = controller
    setThemeStatus(mode)
    setThemeError('')

    // 模型失败或解析不完整时，内置模板与正常结果走同一套选中逻辑
    const applyThemes = (list: ThemeTemplate[]) => {
      setThemes(list)
      setThemeStatus('ready')
      setThemeError('')
      setSelectedThemeId((prev) => (prev && list.some((item) => item.id === prev) ? prev : (list[0]?.id ?? '')))
    }

    generateThemes({ ...payload, modelId }, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        applyThemes(response.themes?.length ? response.themes : FALLBACK_THEMES)
      })
      .catch(() => {
        if (controller.signal.aborted) return
        applyThemes(FALLBACK_THEMES)
      })
      .finally(() => {
        if (themeAbortRef.current === controller) themeAbortRef.current = null
      })
  }

  /** 首页提交：先做前置校验，通过后才进入准备工作台 */
  async function startGeneration(text: string) {
    const message = text.trim()
    if (!message || busy) return

    const resetPrepareState = () => {
      setLecture(null)
      setPendingOutline([])
      setOutlineDraft([])
      setThreadId('')
      setThemes([])
      setSelectedThemeId('')
      setOutlineError('')
      setThemeError('')
      setOutlineStatus('loading')
      setThemeStatus('loading')
      setStage('prepare')
      setPrepareStep('outline')
      setWait(INITIAL_WAIT)
      settledRef.current = false
    }

    cancelPrecheckTask()
    cancelOutlineTask()
    cancelThemeTask()
    cancelActiveRun()

    const controller = new AbortController()
    precheckAbortRef.current = controller
    setPrecheckBusy(true)
    setPrecheckError('')
    try {
      const result = await precheckInput({ message, sessionId, modelId }, controller.signal)
      if (controller.signal.aborted) return

      if (!result.ok) {
        resetPrepareState()
        precheckTokenRef.current = ''
        topicRef.current = ''
        setPrecheckError(result.code || 'NO_TOPIC')
        setView('home')
        return
      }

      const topic = (result.topic || message).trim()
      precheckTokenRef.current = result.token || ''
      sourceMessageRef.current = message
      topicRef.current = topic
      setCourseTitle(topic)
      resetPrepareState()
      setView('prepare')

      runOutlineStream(message, 'loading', { topic, precheckToken: result.token })
      runThemeRequest({ message: topic }, 'loading')
    } catch {
      if (controller.signal.aborted) return
      resetPrepareState()
      precheckTokenRef.current = ''
      setPrecheckError('PRECHECK_ERROR')
      setView('home')
    } finally {
      if (precheckAbortRef.current === controller) precheckAbortRef.current = null
      setPrecheckBusy(false)
    }
  }

  function handleSubmit() {
    void startGeneration(prompt.trim())
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

  /** 返回首页：前置校验、准备阶段两条任务与生成阶段任务全部取消并复位 */
  function handleBackToHome() {
    cancelPrecheckTask()
    cancelOutlineTask()
    cancelThemeTask()
    cancelActiveRun()
    setPrecheckError('')
    precheckTokenRef.current = ''
    setLecture(null)
    setPendingOutline([])
    setOutlineDraft([])
    setThreadId('')
    setThemes([])
    setSelectedThemeId('')
    setWait(INITIAL_WAIT)
    setStage('prepare')
    setPrepareStep('outline')
    setView('home')
  }

  /**
   * 第三步「停止生成」：中断前后端任务并回到准备阶段（即开始生成前的状态），
   * 用户可以回到前两步继续调整，然后再次开始生成。
   */
  function handleInterruptGeneration() {
    cancelActiveRun()
    setWait(INITIAL_WAIT)
    setLecture(null)
    // 旧线程的检查点里可能已经有半成品课件，下一次生成必须开新线程
    setThreadId('')
    topicRef.current = ''
    settledRef.current = true
    streamModeRef.current = 'outline'
    setStage('prepare')
    setPrepareStep('theme')
  }

  /** 生成页「重新生成」：用同一段输入重新进入准备台 */
  function handleRetry() {
    void startGeneration(sourceMessageRef.current || prompt.trim())
  }

  /** 大纲编辑实时落到草稿，切步骤时不会丢失手动修改 */
  const handleOutlineDraftChange = useCallback((catalog: OutlineModule[]) => {
    setOutlineDraft(catalog)
  }, [])

  /** 大纲面板「下一步」：保存当前大纲并切到主题选择 */
  function handleOutlineConfirmed(catalog: OutlineModule[]) {
    setPendingOutline(catalog)
    setOutlineDraft(catalog)
  }

  /** 大纲面板「重新生成大纲」：把原始主题与用户新增页面一起交给模型重跑大纲流 */
  function handleRegenerateOutline(payload: { catalogList: OutlineModule[] }) {
    const topic = topicRef.current || sourceMessageRef.current || courseTitle
    const addedHints = payload.catalogList
      .filter((module) => module.isNew)
      .map((module, index) => {
        const title = (module.title[0] ?? '').trim()
        const summary = (module.summary ?? '').trim()
        return `新增页面 ${index + 1}：主题「${title}」${summary ? `，简介「${summary}」` : ''}`
      })
    const parts = [
      `请重新生成课程大纲。原始课程主题：${topic}`,
      '整套课件必须围绕原始课程主题，不要缩窄成某个子玩法或具体条件；第 1 页应是该主题的整体入门。',
      '请保留由浅入深的结构，title 与 context 等长，并为每个模块写出简介。',
      '请把用户新增的页面纳入新大纲，并在对应模块中体现其主题与简介。',
    ]
    setPendingOutline([])
    setOutlineDraft([])
    setThreadId('')
    runOutlineStream(sourceMessageRef.current || courseTitle, 'regenerating', {
      topic,
      outlineHint: [parts.join('\n'), addedHints.join('\n')].filter(Boolean).join('\n'),
      precheckToken: precheckTokenRef.current,
    })
  }

  /** 主题面板「重新生成主题」：把意见与当前候选一起交给模型调整 */
  function handleRegenerateTheme(feedback: string) {
    if (!feedback) return
    runThemeRequest(
      {
        message: sourceMessageRef.current || courseTitle,
        feedback,
        currentThemes: themes,
      },
      'regenerating',
    )
  }

  /**
   * 第三步的实际开跑逻辑：用当前大纲与所选样式请求后端生成。
   * 「主题选择」的开始生成课件与失败后的「重新生成」共用同一份数据与同一起点。
   * 每次都用新线程，避免停止生成后恢复旧线程带回上一次的课件产物。
   */
  function runCourseGeneration() {
    const theme = themes.find((item) => item.id === selectedThemeId)
    const outlineList = outlineDraft.length ? outlineDraft : pendingOutline
    if (!theme || !outlineList.length) return
    const controller = new AbortController()
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    abortRef.current = controller
    runIdRef.current = runId
    const topic = sourceMessageRef.current || courseTitle || theme.name
    topicRef.current = topic
    streamModeRef.current = 'course'
    settledRef.current = false
    setWait(INITIAL_WAIT)
    setLecture(null)
    setStage('generate')
    setPrepareStep('generate')

    generateCourse(
      { topic, catalogList: outlineList, theme, sessionId, runId, modelId },
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
        }
      })
  }

  /** 主题面板「开始生成课件」：用当前大纲与所选样式从第三步起点开跑 */
  function handleStartGeneration() {
    if (view !== 'prepare' || busy) return
    runCourseGeneration()
  }

  /**
   * 第三步「重新生成」：生成失败后按当前数据从第三步起点重跑。
   * 与主题面板的「开始生成课件」完全一致：同一份大纲、同一样式、同样的图起点。
   */
  function handleRegenerateGeneration() {
    if (view !== 'prepare') return
    // 失败事件到达时旧 SSE 可能还没断开，先兜底中断，避免两条流同时写状态
    cancelActiveRun()
    setThreadId('')
    runCourseGeneration()
  }

  /** 结果页「重试失败页」：图外直调重跑，沿用最初选择的 PPT 样式 */
  function handleRetryFailed() {
    if (busy) return
    const theme = themes.find((item) => item.id === selectedThemeId)
    const controller = new AbortController()
    const runId = `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    abortRef.current = controller
    runIdRef.current = runId
    streamModeRef.current = 'course'
    settledRef.current = false
    setWait((prev) => ({ ...prev, status: 'retrying', step: 'retry', message: '正在重试失败的页面…' }))

    retryFailedPages({ sessionId, runId, modelId, theme }, handleEvent, controller.signal)
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
        }
      })
  }

  /** 生成页「进入课堂」：进入课件播放页 */
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
    setStage('prepare')
    setPrepareStep('outline')
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
    const models = modelGroups.flatMap((group) => group.models)
    // 只定位展开哪个服务商；不在这里改 modelId，避免配置刷新失败时覆盖用户偏好
    const current = models.find((model) => model.id === modelId) ?? models[0]
    setActiveProviderId(current?.providerId ?? DEFAULT_PROVIDER_ID)
    setProviderSearch('')
    setModelMenuOpen((prev) => !prev)
  }

  if (view === 'prepare') {
    return (
      <PrepareWorkspace
        courseTitle={courseTitle}
        catalogList={pendingOutline}
        outlineStatus={outlineStatus}
        outlineError={outlineError}
        themes={themes}
        themeStatus={themeStatus}
        themeError={themeError}
        selectedThemeId={selectedThemeId}
        outlineDraft={outlineDraft}
        onBack={handleBackToHome}
        onOutlineDraftChange={handleOutlineDraftChange}
        onOutlineConfirmed={handleOutlineConfirmed}
        onRegenerateOutline={handleRegenerateOutline}
        onSelectTheme={setSelectedThemeId}
        onRegenerateTheme={handleRegenerateTheme}
        onStartGeneration={handleStartGeneration}
        activeStep={prepareStep}
        onStepChange={setPrepareStep}
        generating={stage === 'generate'}
        generation={{
          status: wait.status,
          step: wait.step,
          pagesCurrent: wait.pagesCurrent,
          pagesTotal: wait.pagesTotal,
          audioCurrent: wait.audioCurrent,
          audioTotal: wait.audioTotal,
          message: wait.message,
        }}
        lecture={lecture}
        onInterruptGeneration={handleInterruptGeneration}
        onStartLecture={handleStart}
        onRetryFailed={handleRetryFailed}
        onRetryGeneration={handleRegenerateGeneration}
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

  if (view === 'generating') {
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
        courseTitle={courseTitle}
        themeName={themes.find((item) => item.id === selectedThemeId)?.name}
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
          <span className="composer-tip">
            {precheckBusy ? '正在校验输入内容…' : t('composer.ready')}
          </span>
        </div>
        {precheckError ? (
          <div className="composer-error" role="alert">
            {precheckMessage(precheckError)}
          </div>
        ) : null}

        <label className="sr-only" htmlFor="composer-input">
          {t('composer.placeholderPrefix')}
        </label>
        <textarea
          id="composer-input"
          className="composer-input"
          value={prompt}
          placeholder={placeholder}
          onChange={(event) => {
            setPrompt(event.target.value)
            // 开始修改输入时立即清掉违规 / 未检测到主题的提示，并取消在途校验，
            // 避免旧请求返回后把提示又写回来
            setPrecheckError('')
            cancelPrecheckTask()
          }}
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
                          selectModel(model.id)
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
            <span>{precheckBusy ? '校验中…' : busy ? t('composer.generating') : t('composer.enter')}</span>
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
