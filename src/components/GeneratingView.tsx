import { useEffect, useMemo, useState } from 'react'
import { friendlyErrorMessage } from '../api/chat'
import type { ChatResponse, ProgressStep } from '../api/chat'
import { useI18n } from '../i18n/context'
import './GeneratingView.css'

/** 等待页的四态 + 重试态 + 用户主动中断态 */
export type WaitStatus = 'running' | 'ready' | 'reply' | 'failed' | 'retrying' | 'interrupted'

interface GeneratingViewProps {
  /** 当前整体状态 */
  status: WaitStatus
  /** 当前所处步骤；null 表示尚未收到进度 */
  step: ProgressStep | null
  /** 页面生成：已完成 / 总数 */
  pagesCurrent: number
  pagesTotal: number
  /** 音频合成：已完成 / 总数 */
  audioCurrent: number
  audioTotal: number
  /** 闲聊回复文本或错误信息 */
  message: string
  /** 讲解主题 */
  topic: string
  /** 首页原始输入，展示在生成页顶部摘要 */
  courseTitle?: string
  /** 用户所选 PPT 样式名称 */
  themeName?: string
  /** 课件就绪后的产物（ready 态展示与降级提示用） */
  lecture: ChatResponse | null
  onBack: () => void
  /** 中断生成（仅生成中可用） */
  onInterrupt: () => void
  onRetry: () => void
  onStart: () => void
  /** 重试失败页（partial 降级交付时的入口） */
  onRetryFailed?: () => void
}

/** 步骤顺序（confirm / retry 按需出现；前置阶段不在清单中展示） */
const STEP_KEYS: ProgressStep[] = ['pages', 'audio', 'quiz', 'assemble', 'confirm', 'retry', 'done']

function CheckIcon() {
  return (
    <svg className="gv-check" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 6 9 17l-5-5"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/**
 * 生成等待页：深色科技感 + 玻璃卡片 + 步骤清单微动效。
 * ready 态即结果页（标题「讲解完成」），partial 降级时展示失败清单与重试入口。
 */
export default function GeneratingView({
  status,
  step,
  pagesCurrent,
  pagesTotal,
  audioCurrent,
  audioTotal,
  message,
  topic,
  courseTitle,
  themeName,
  lecture,
  onBack,
  onInterrupt,
  onRetry,
  onStart,
  onRetryFailed,
}: GeneratingViewProps) {
  const { t } = useI18n()
  const [elapsed, setElapsed] = useState(0)

  const stepLabels: Record<string, string> = {
    pages: t('generate.step.pages'),
    audio: t('generate.step.audio'),
    quiz: t('generate.step.quiz'),
    assemble: t('generate.step.assemble'),
    confirm: t('generate.step.confirm'),
    retry: t('generate.step.retry'),
    done: t('generate.step.done'),
  }
  const runningTitles: Record<string, string> = {
    check: t('gen.running.check'),
    safety: t('gen.running.safety'),
    topic: t('gen.running.topic'),
    outline: t('gen.running.outline'),
    pages: t('gen.running.pages'),
    audio: t('gen.running.audio'),
    quiz: t('gen.running.quiz'),
    assemble: t('gen.running.assemble'),
    confirm: t('gen.running.confirm'),
    retry: t('gen.running.retry'),
  }

  useEffect(() => {
    const timer = window.setInterval(() => setElapsed((prev) => prev + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])

  // 生成中或重试中才走步骤清单；其余态按结果页展示
  const running = status === 'running' || status === 'retrying'
  const ready = status === 'ready'
  const failed = status === 'failed'
  const replied = status === 'reply'
  const interrupted = status === 'interrupted'

  const activeStep: ProgressStep | null = status === 'retrying' ? 'retry' : step

  // 步骤可见性：confirm / retry 只在出现时占位，其余常驻
  const visibleSteps = useMemo(
    () =>
      STEP_KEYS.filter((key) => {
        if (key === 'confirm' || key === 'retry') {
          return activeStep === key || (key === 'retry' && status === 'retrying')
        }
        return true
      }).map((key) => ({ key, label: stepLabels[key] ?? key })),
    [activeStep, status, stepLabels],
  )

  const activeIndex = visibleSteps.findIndex((def) => def.key === activeStep)

  const failedPages = lecture?.failedPages ?? []
  const partial = Boolean(lecture?.partial) || failedPages.length > 0

  const title = ready
    ? t('gen.titleReady')
    : failed
      ? t('generate.failed')
      : interrupted
        ? t('gen.titleInterrupted')
        : replied
          ? t('gen.titleReply')
          : status === 'retrying'
            ? t('gen.titleRetrying')
            : t('gen.titleRunning')

  const subtitle = ready
    ? partial
      ? t('gen.subtitleReadyPartial')
      : t('generate.subtitleReady')
    : failed
      ? friendlyErrorMessage(message, t('gen.errorBanner')) || t('gen.subtitleFailed')
      : interrupted
        ? t('gen.subtitleInterrupted')
        : replied
          ? t('gen.subtitleReply')
          : (activeStep && runningTitles[activeStep]) || t('gen.subtitleRunning')

  return (
    <div className="gv-page">
      <button className="gv-back" type="button" onClick={onBack}>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M15 18l-6-6 6-6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {t('gen.back')}
      </button>

      <div className="gv-stage-head">
        <div className="gv-stage-steps" aria-label={t('prepare.flowAria')}>
          <span className="gv-stage-step is-done">{t('step.outline')}</span>
          <span className="gv-stage-line" />
          <span className="gv-stage-step is-done">{t('step.theme')}</span>
          <span className="gv-stage-line" />
          <span className="gv-stage-step is-current">{t('step.generate')}</span>
        </div>
        <div className="gv-stage-summary">
          <span title={courseTitle || topic}>{courseTitle || topic || t('prepare.newCourse')}</span>
          {themeName ? <span className="gv-stage-theme">{t('gen.style', { name: themeName })}</span> : null}
        </div>
      </div>

      <div className="gv-orb gv-orb-blue" />
      <div className="gv-orb gv-orb-purple" />

      <div className={`gv-card ${ready ? 'is-ready' : ''}`}>
        <div className="gv-dots">
          <div className={`gv-dot ${ready || activeIndex > 2 ? 'is-done' : running && activeIndex <= 2 ? 'is-active is-pulse' : ''}`} />
          <div className={`gv-dot ${ready ? 'is-done' : running && activeIndex > 2 ? 'is-active is-pulse' : ''}`} />
          <div className={`gv-dot ${ready ? 'is-done' : ''}`} />
        </div>

        <div className={`gv-badge ${ready ? 'is-ready' : failed || interrupted ? 'is-failed' : ''}`}>
          {ready || failed || interrupted ? <CheckIcon /> : <div className="gv-badge-core" />}
        </div>

        <h1 className="gv-title">{title}</h1>
        <p className="gv-subtitle">{subtitle}</p>

        {running ? (
          <ul className="gv-steps">
            {visibleSteps.map((def, idx) => {
              const done = activeIndex >= 0 && idx < activeIndex
              const active = idx === activeIndex
              let count = ''
              if (def.key === 'pages' && pagesTotal > 0) count = `${pagesCurrent} / ${pagesTotal}`
              else if (def.key === 'audio' && audioTotal > 0) count = `${audioCurrent} / ${audioTotal}`
              return (
                <li key={def.key} className={`gv-step ${done ? 'is-done' : ''} ${active ? 'is-active' : ''}`}>
                  <span className="gv-step-mark">{done ? <CheckIcon /> : <i />}</span>
                  <span className="gv-step-name">{def.label}</span>
                  {count ? (
                    <span className="gv-step-count" title={count}>
                      {count}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ul>
        ) : null}

        {replied && message ? <div className="gv-reply">{message}</div> : null}

        {status === 'retrying' ? (
          <div className="gv-banner is-warn">{message || t('gen.retryBanner')}</div>
        ) : null}

        {failed ? <div className="gv-banner is-error">{friendlyErrorMessage(message, t('gen.errorBanner')) || t('gen.errorBanner')}</div> : null}

        {interrupted ? <div className="gv-banner is-warn">{message || t('gen.interruptedBanner')}</div> : null}

        {ready && partial ? (
          <div className="gv-banner is-warn">
            {t('generate.failedHint')}
            {failedPages.map((row) => (
              <div key={`${row.ind}-${row.stage}`} className="gv-failed-item">
                {t('generate.failedPageLine', {
                  n: row.ind,
                  reason: t(row.stage === 'audio' ? 'generate.failedAudio' : 'generate.failedPage'),
                })}
                {row.error ? t('generate.failedError', { error: row.error }) : ''}
              </div>
            ))}
          </div>
        ) : null}

        {running ? (
          <button className="gv-btn is-danger" type="button" onClick={onInterrupt}>
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
            {t('generate.stop')}
          </button>
        ) : null}

        {ready ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onStart}>
              {t('generate.enterClass')}
            </button>
            {partial && onRetryFailed ? (
              <button className="gv-btn is-ghost" type="button" onClick={onRetryFailed}>
                {t('generate.retryFailed')}
              </button>
            ) : null}
          </>
        ) : null}

        {interrupted ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onRetry}>
              {t('generate.regenerate')}
            </button>
            <button className="gv-btn is-ghost" type="button" onClick={onBack}>
              {t('gen.backHome')}
            </button>
          </>
        ) : null}

        {failed ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onRetry}>
              {t('generate.regenerate')}
            </button>
            <button className="gv-btn is-ghost" type="button" onClick={onBack}>
              {t('gen.backHome')}
            </button>
          </>
        ) : null}

        {replied ? (
          <button className="gv-btn is-ghost" type="button" onClick={onBack}>
            {t('gen.backHome')}
          </button>
        ) : null}

        {ready ? (
          <div className="gv-meta">
            {topic ? t('gen.topicLabel', { value: topic }) : ''}
            {lecture?.pageList?.length ? t('gen.pagesLabel', { n: lecture.pageList.length }) : ''}
            {partial ? t('gen.partialSuccess') : t('gen.allSuccess')}
          </div>
        ) : null}
      </div>

      {running ? (
        <div className="gv-foot">
          <div className="gv-spinner" />
          <span>{status === 'retrying' ? t('gen.footRetrying') : t('gen.footRunning')}</span>
          <span className="gv-clock">{t('gen.elapsed', { n: elapsed })}</span>
        </div>
      ) : null}
    </div>
  )
}
