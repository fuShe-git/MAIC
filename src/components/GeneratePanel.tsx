import { friendlyErrorMessage } from '../api/chat'
import type { ChatResponse, ProgressStep } from '../api/chat'
import { useI18n } from '../i18n/context'
import type { WaitStatus } from './GeneratingView'

interface GeneratePanelProps {
  status: WaitStatus
  step: ProgressStep | null
  pagesCurrent: number
  pagesTotal: number
  audioCurrent: number
  audioTotal: number
  message: string
  lecture: ChatResponse | null
  /** 停止生成：中断前后端任务并回到上一步 */
  onInterrupt: () => void
  /** 进入课堂 */
  onStart: () => void
  /** 重试失败页（降级交付时可用） */
  onRetryFailed?: () => void
  /** 重新生成（暂时隐藏按钮，逻辑保留待测试） */
  onRetry?: () => void
}

/** 进度清单顺序：前置阶段不展示，从页面生成开始 */
const STEP_KEYS: ProgressStep[] = ['pages', 'audio', 'quiz', 'assemble', 'retry', 'done']

function CheckIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 6 9 17l-5-5"
        stroke="currentColor"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** 工作台第三步：课件生成进度，沿用准备台的步骤条与卡片样式。 */
export default function GeneratePanel({
  status,
  step,
  pagesCurrent,
  pagesTotal,
  audioCurrent,
  audioTotal,
  message,
  lecture,
  onInterrupt,
  onStart,
  onRetryFailed,
  onRetry,
}: GeneratePanelProps) {
  const { t } = useI18n()
  const running = status === 'running' || status === 'retrying'
  const ready = status === 'ready'
  const failed = status === 'failed'

  const stepLabels: Record<string, string> = {
    pages: t('generate.step.pages'),
    audio: t('generate.step.audio'),
    quiz: t('generate.step.quiz'),
    assemble: t('generate.step.assemble'),
    confirm: t('generate.step.confirm'),
    retry: t('generate.step.retry'),
    done: t('generate.step.done'),
  }
  const activeStep: ProgressStep | null = status === 'retrying' ? 'retry' : step
  const visibleSteps = STEP_KEYS.filter((key) => key !== 'retry' || activeStep === 'retry').map(
    (key) => ({ key, label: stepLabels[key] ?? key }),
  )
  const activeIndex = visibleSteps.findIndex((def) => def.key === activeStep)

  const failedPages = lecture?.failedPages ?? []
  const partial = Boolean(lecture?.partial) || failedPages.length > 0

  const displayMessage = friendlyErrorMessage(message, t('gen.errorBanner'))
  const title = ready ? t('generate.ready') : failed ? t('generate.failed') : t('generate.title')
  const subtitle = ready
    ? partial
      ? t('generate.subtitleReadyPartial')
      : t('generate.subtitleReady')
    : failed
      ? displayMessage || t('generate.subtitleFailed')
      : t('generate.subtitleRunning')

  return (
    <section className="pw-fade-in flex min-h-0 flex-1 flex-col gap-3">
      <header className="shrink-0">
        <p className="m-0 text-[13px] leading-5 text-[var(--text-dim)]">{t('generate.hint')}</p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1 pw-scroll">
        <div className="flex flex-col items-center px-2 py-4 text-center">
          <span
            className={`flex h-14 w-14 items-center justify-center rounded-full text-white ${
              ready
                ? 'bg-[#3E9D6B] shadow-[0_14px_26px_-12px_rgba(62,157,107,0.8)]'
                : failed
                  ? 'bg-[#B3261E] shadow-[0_14px_26px_-12px_rgba(179,38,30,0.8)]'
                  : 'animate-pulse bg-[#5B45E0] shadow-[0_14px_26px_-12px_rgba(91,69,224,0.85)]'
            }`}
          >
            {ready ? (
              <CheckIcon />
            ) : failed ? (
              <span className="text-[20px] font-bold leading-none">!</span>
            ) : (
              <i className="block h-2.5 w-2.5 rounded-full bg-white" />
            )}
          </span>

          <h3 className="mt-3 text-[16px] font-semibold text-[var(--text)]">{title}</h3>
          <p className="mt-1 text-[12px] text-[var(--text-faint)]">{subtitle}</p>

          {failed && onRetry ? (
            <button
              type="button"
              className="mt-4 cursor-pointer rounded-xl bg-gradient-to-r from-[#5B45E0] to-[#8B7BFF] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_10px_24px_-12px_rgba(91,69,224,0.9)] transition hover:brightness-110"
              onClick={onRetry}
            >
              {t('generate.regenerate')}
            </button>
          ) : null}

          {running ? (
            <ul className="mt-5 w-full max-w-[560px] space-y-1 rounded-2xl border border-[var(--border)] bg-[var(--surface-2)] p-3 text-left">
              {visibleSteps.map((def, idx) => {
                const done = activeIndex >= 0 && idx < activeIndex
                const active = idx === activeIndex
                let count = ''
                if (def.key === 'pages' && pagesTotal > 0) count = `${pagesCurrent} / ${pagesTotal}`
                else if (def.key === 'audio' && audioTotal > 0) count = `${audioCurrent} / ${audioTotal}`
                return (
                  <li key={def.key} className="flex items-center gap-2 rounded-lg px-2 py-1.5">
                    <span
                      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full ${
                        done
                          ? 'bg-[#3E9D6B] text-white'
                          : active
                            ? 'bg-[#5B45E0] text-white'
                            : 'border border-[var(--border-strong)] text-transparent'
                      }`}
                    >
                      {done ? (
                        <CheckIcon />
                      ) : active ? (
                        <i className="block h-1.5 w-1.5 rounded-full bg-white" />
                      ) : null}
                    </span>
                    <span
                      className={`flex-1 text-[13px] ${
                        active
                          ? 'font-semibold text-[var(--text)]'
                          : done
                            ? 'text-[var(--text-dim)]'
                            : 'text-[var(--text-faint)]'
                      }`}
                    >
                      {def.label}
                    </span>
                    {count ? <span className="text-[12px] text-[var(--text-faint)]">{count}</span> : null}
                  </li>
                )
              })}
            </ul>
          ) : null}

          {ready && partial ? (
            <div className="mt-4 w-full max-w-[560px] rounded-xl border border-[var(--border)] bg-[var(--surface-hover)] p-3 text-left text-[12px] text-[var(--text-dim)]">
              {t('generate.failedHint')}
              {failedPages.map((row) => (
                <div key={`${row.ind}-${row.stage}`} className="mt-1">
                  {t('generate.failedPageLine', {
                    n: row.ind,
                    reason: t(row.stage === 'audio' ? 'generate.failedAudio' : 'generate.failedPage'),
                  })}
                  {row.error ? t('generate.failedError', { error: row.error }) : ''}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2">
        {running ? (
          <button
            type="button"
            className="cursor-pointer rounded-xl border border-[#F2C7C7] bg-[#FDECEC] px-4 py-2 text-[13px] font-medium text-[#B3261E] transition hover:bg-[#FBDDDD]"
            onClick={onInterrupt}
          >
            {t('generate.stop')}
          </button>
        ) : null}
        {ready ? (
          <button
            type="button"
            className="cursor-pointer rounded-xl bg-gradient-to-r from-[#5B45E0] to-[#8B7BFF] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_10px_24px_-12px_rgba(91,69,224,0.9)] transition hover:brightness-110"
            onClick={onStart}
          >
            {t('generate.enterClass')}
          </button>
        ) : null}
        {ready && partial && onRetryFailed ? (
          <button
            type="button"
            className="cursor-pointer rounded-xl border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[13px] font-medium text-[var(--text-dim)] transition hover:text-[var(--text)]"
            onClick={onRetryFailed}
          >
            {t('generate.retryFailed')}
          </button>
        ) : null}
      </footer>
    </section>
  )
}
