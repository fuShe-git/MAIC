import { useEffect, useMemo, useState } from 'react'
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

/** 步骤清单（confirm / retry 按需出现；前置阶段不在清单中展示） */
const STEP_DEFS: Array<{ key: ProgressStep; label: string }> = [
  { key: 'pages', label: '生成页面' },
  { key: 'audio', label: '合成音频' },
  { key: 'quiz', label: '课后练习' },
  { key: 'assemble', label: '组装课件' },
  { key: 'confirm', label: '等待确认大纲' },
  { key: 'retry', label: '重试失败页' },
  { key: 'done', label: '生成完毕' },
]

const RUNNING_TITLES: Record<string, string> = {
  check: '正在理解你的讲解需求',
  safety: '正在做内容安全审核',
  topic: '正在确认讲解主题',
  outline: '正在设计讲解大纲',
  pages: '正在生成讲解页面',
  audio: '正在合成讲解音频',
  quiz: '正在生成课后练习',
  assemble: '正在组装课件',
  confirm: '等待你确认大纲',
  retry: '正在重试失败的页面',
}

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
      STEP_DEFS.filter((def) => {
        if (def.key === 'confirm' || def.key === 'retry') {
          return activeStep === def.key || (def.key === 'retry' && status === 'retrying')
        }
        return true
      }),
    [activeStep, status],
  )

  const activeIndex = visibleSteps.findIndex((def) => def.key === activeStep)

  const failedPages = lecture?.failedPages ?? []
  const partial = Boolean(lecture?.partial) || failedPages.length > 0

  const title = ready
    ? '讲解完成'
    : failed
      ? '生成失败'
      : interrupted
        ? '生成已中断'
        : replied
          ? 'MAIC 助手'
          : status === 'retrying'
            ? '正在重试生成'
            : '正在生成讲解'

  const subtitle = ready
    ? partial
      ? '讲解已就绪，但有部分内容降级交付'
      : '课件已就绪，可以进入课堂了'
    : failed
      ? '很抱歉，生成过程中出了点问题'
      : interrupted
        ? '本次生成已停止，可重新生成或返回首页'
        : replied
          ? '这次没有生成课件，先聊聊吧'
          : (activeStep && RUNNING_TITLES[activeStep]) || '正在为你生成讲解'

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
        返回
      </button>

      <div className="gv-stage-head">
        <div className="gv-stage-steps" aria-label="生成流程步骤">
          <span className="gv-stage-step is-done">{t('step.outline')}</span>
          <span className="gv-stage-line" />
          <span className="gv-stage-step is-done">{t('step.theme')}</span>
          <span className="gv-stage-line" />
          <span className="gv-stage-step is-current">{t('step.generate')}</span>
        </div>
        <div className="gv-stage-summary">
          <span title={courseTitle || topic}>{courseTitle || topic || '新的课程'}</span>
          {themeName ? <span className="gv-stage-theme">样式 · {themeName}</span> : null}
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
          <div className="gv-banner is-warn">{message || '正在重新生成，请稍候…'}</div>
        ) : null}

        {failed ? <div className="gv-banner is-error">{message || '生成出错，请重试'}</div> : null}

        {interrupted ? <div className="gv-banner is-warn">{message || '你已中断本次生成'}</div> : null}

        {ready && partial ? (
          <div className="gv-banner is-warn">
            以下内容生成失败，已降级交付：
            {failedPages.map((row) => (
              <div key={`${row.ind}-${row.stage}`} className="gv-failed-item">
                第 {row.ind} 页 · {row.stage === 'audio' ? '音频合成失败' : '页面生成失败'}
                {row.error ? `（${row.error}）` : ''}
              </div>
            ))}
          </div>
        ) : null}

        {running ? (
          <button className="gv-btn is-danger" type="button" onClick={onInterrupt}>
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
            停止生成
          </button>
        ) : null}

        {ready ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onStart}>
              进入课堂
            </button>
            {partial && onRetryFailed ? (
              <button className="gv-btn is-ghost" type="button" onClick={onRetryFailed}>
                重试失败页
              </button>
            ) : null}
          </>
        ) : null}

        {interrupted ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onRetry}>
              重新生成
            </button>
            <button className="gv-btn is-ghost" type="button" onClick={onBack}>
              返回首页
            </button>
          </>
        ) : null}

        {failed ? (
          <>
            <button className="gv-btn is-primary" type="button" onClick={onRetry}>
              重新生成
            </button>
            <button className="gv-btn is-ghost" type="button" onClick={onBack}>
              返回首页
            </button>
          </>
        ) : null}

        {replied ? (
          <button className="gv-btn is-ghost" type="button" onClick={onBack}>
            返回首页
          </button>
        ) : null}

        {ready ? (
          <div className="gv-meta">
            {topic ? `主题：${topic}　` : ''}
            {lecture?.pageList?.length ? `${lecture.pageList.length} 页　` : ''}
            {partial ? '部分成功' : '全部成功'}
          </div>
        ) : null}
      </div>

      {running ? (
        <div className="gv-foot">
          <div className="gv-spinner" />
          <span>{status === 'retrying' ? '正在重试，预计还需 1 分钟' : '正在为你生成讲解，预计 1-2 分钟'}</span>
          <span className="gv-clock">已用时 {elapsed}s</span>
        </div>
      ) : null}
    </div>
  )
}
