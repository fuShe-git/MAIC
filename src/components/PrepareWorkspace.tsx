import { Fragment } from 'react'
import type { ChatResponse, OutlineModule, ProgressStep } from '../api/chat'
import type { ThemeTemplate } from '../api/theme'
import ConfirmView from './ConfirmView'
import type { OutlineEditPayload } from './ConfirmView'
import ThemeSelectPanel from './ThemeSelectPanel'
import GeneratePanel from './GeneratePanel'
import type { WaitStatus } from './GeneratingView'
import './PrepareWorkspace.css'

export type OutlinePanelStatus = 'loading' | 'ready' | 'regenerating' | 'error'
export type ThemePanelStatus = 'loading' | 'ready' | 'regenerating' | 'error'
export type PrepareStep = 'outline' | 'theme' | 'generate'

/** 课件生成阶段的状态快照 */
export interface GenerationState {
  status: WaitStatus
  step: ProgressStep | null
  pagesCurrent: number
  pagesTotal: number
  audioCurrent: number
  audioTotal: number
  message: string
}

interface PrepareWorkspaceProps {
  courseTitle: string
  catalogList: OutlineModule[]
  outlineStatus: OutlinePanelStatus
  outlineError: string
  themes: ThemeTemplate[]
  themeStatus: ThemePanelStatus
  themeError: string
  selectedThemeId: string
  outlineDraft: OutlineModule[]
  onBack: () => void
  /** 大纲编辑实时回传，切步骤时保留用户修改 */
  onOutlineDraftChange: (catalogList: OutlineModule[]) => void
  onOutlineConfirmed: (catalogList: OutlineModule[]) => void
  onRegenerateOutline: (payload: OutlineEditPayload) => void
  onSelectTheme: (themeId: string) => void
  onRegenerateTheme: (feedback: string) => void
  onStartGeneration: () => void
  /** 当前所处步骤（由外层控制） */
  activeStep: PrepareStep
  onStepChange: (step: PrepareStep) => void
  /** 是否已进入第三步（课件生成） */
  generating: boolean
  generation: GenerationState
  lecture: ChatResponse | null
  /** 停止生成：中断任务并回到上一步 */
  onInterruptGeneration: () => void
  /** 进入课堂 */
  onStartLecture: () => void
  /** 重试失败页 */
  onRetryFailed: () => void
  /** 重新生成（按钮暂时隐藏） */
  onRetryGeneration: () => void
}

const STEPS: Array<{
  key: PrepareStep
  label: string
  hint: string
}> = [
  { key: 'outline', label: '大纲确认', hint: '规划课程结构' },
  { key: 'theme', label: '主题选择', hint: '选定课件样式' },
  { key: 'generate', label: '课件生成', hint: '进入课堂' },
]

/** 准备工作台：三段式导航中的前两段，大纲与样式各自独立加载、调试。 */
export default function PrepareWorkspace({
  courseTitle,
  catalogList,
  outlineStatus,
  outlineError,
  themes,
  themeStatus,
  themeError,
  selectedThemeId,
  outlineDraft,
  onBack,
  onOutlineDraftChange,
  onOutlineConfirmed,
  onRegenerateOutline,
  onSelectTheme,
  onRegenerateTheme,
  onStartGeneration,
  activeStep,
  onStepChange,
  generating,
  generation,
  lecture,
  onInterruptGeneration,
  onStartLecture,
  onRetryFailed,
  onRetryGeneration,
}: PrepareWorkspaceProps) {
  /** 切到主题选择时同步提交当前大纲（替代原“下一步”按钮） */
  const switchStep = (step: 'outline' | 'theme') => {
    if (step === 'theme') onOutlineConfirmed(outlineDraft)
    onStepChange(step)
  }

  // 仅第三步激活且进度仍在推进时，才锁定前两步
  const generatingNow =
    generating && (generation.status === 'running' || generation.status === 'retrying')

  const outlineReady = outlineStatus === 'ready' && outlineDraft.length > 0
  const themeReady = themeStatus === 'ready' && themes.length > 0 && Boolean(selectedThemeId)
  const canStart = outlineReady && themeReady
  const startDisabledReason = !outlineReady && !themeReady
    ? '大纲和主题暂未就绪'
    : !outlineReady
      ? '大纲暂未就绪'
      : !themeReady
        ? '主题暂未就绪'
        : '按当前大纲和所选样式生成课件'

  const statusLabel = (status: OutlinePanelStatus | ThemePanelStatus) =>
    status === 'ready' ? '已生成' : status === 'error' ? '需重试' : '生成中'

  return (
    <div className="pw-workspace flex h-svh flex-col overflow-hidden bg-[var(--page-bg)] text-[var(--text)]">
      <header className="sticky top-0 z-20 border-b border-[var(--border)] bg-[var(--chrome-bg)] backdrop-blur-xl">
        <div className="mx-auto flex w-full max-w-[1240px] items-center gap-3 px-4 py-3">
          <button
            type="button"
            onClick={onBack}
            className="cursor-pointer rounded-xl border border-[var(--border)] bg-[var(--surface-2)] px-3 py-1.5 text-[13px] text-[var(--text-dim)] transition hover:text-[var(--text)]"
          >
            ← 返回
          </button>
          <div className="min-w-0 flex-1 text-center">
            <p className="m-0 text-[11px] tracking-[0.24em] text-[var(--text-faint)]">MAIC · 课堂生成</p>
            <h1 className="m-0 truncate text-[16px] font-semibold">{courseTitle || '新的课程'}</h1>
          </div>
          <div className="hidden w-[84px] md:block" />
        </div>
      </header>

      <main className="mx-auto flex min-h-0 w-full max-w-[1240px] flex-1 flex-col gap-3 overflow-hidden px-4 py-5">
        <nav className="pw-steps flex shrink-0 items-stretch rounded-2xl border border-[var(--border)] bg-[var(--surface-2)]" aria-label="准备流程">
          {STEPS.map((step, index) => {
            const active = activeStep === step.key
            const third = step.key === 'generate'
            const clickable = third ? generating : !generatingNow
            const status = step.key === 'outline' ? outlineStatus : step.key === 'theme' ? themeStatus : null
            return (
              <Fragment key={step.key}>
                {index > 0 ? <span className="pw-step-divider w-px shrink-0 bg-[var(--border)]" aria-hidden="true" /> : null}
                <button
                  type="button"
                  onClick={() => {
                    if (step.key === 'generate') {
                      if (generating) onStepChange('generate')
                      return
                    }
                    if (generatingNow) return
                    switchStep(step.key)
                  }}
                  disabled={!clickable}
                  aria-current={active ? 'step' : undefined}
                  className={`pw-step flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left transition ${
                    clickable ? 'cursor-pointer' : 'cursor-default'
                  } ${active ? 'pw-step-active' : ''}`}
                >
                  <span className={`pw-step-index flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold ${active ? 'is-active' : status === 'ready' ? 'is-done' : ''}`}>
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={`block truncate text-[13px] font-semibold ${active ? 'text-[var(--accent)]' : 'text-[var(--text)]'}`}>
                      {step.label}
                    </span>
                    <span className="block truncate text-[11px] text-[var(--text-faint)]">{step.hint}</span>
                  </span>
                  {status ? (
                    <span
                      className="hidden shrink-0 items-center justify-center sm:flex"
                      title={statusLabel(status)}
                    >
                      {status === 'ready' ? (
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#3E9D6B] text-white">
                          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path
                              d="M20 6 9 17l-5-5"
                              stroke="currentColor"
                              strokeWidth="3"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </span>
                      ) : status === 'error' ? (
                        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#B3261E] text-[11px] font-bold leading-none text-white">
                          !
                        </span>
                      ) : (
                        <span className="pw-step-spinner h-4 w-4 rounded-full border-2 border-[var(--border-strong)] border-t-[#5B45E0]" />
                      )}
                    </span>
                  ) : null}
                </button>
              </Fragment>
            )
          })}
        </nav>

        <div className="pw-glass flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl p-4 md:p-5">
          {activeStep === 'outline' ? (
            <ConfirmView
              catalogList={catalogList}
              status={outlineStatus}
              errorMessage={outlineError}
              onRegenerateOutline={onRegenerateOutline}
              onDraftChange={onOutlineDraftChange}
            />
          ) : activeStep === 'theme' ? (
            <ThemeSelectPanel
              themes={themes}
              status={themeStatus}
              errorMessage={themeError}
              selectedThemeId={selectedThemeId}
              canStart={canStart}
              startDisabledReason={startDisabledReason}
              onSelect={onSelectTheme}
              onRegenerateTheme={onRegenerateTheme}
              onStart={onStartGeneration}
            />
          ) : (
            <GeneratePanel
              status={generation.status}
              step={generation.step}
              pagesCurrent={generation.pagesCurrent}
              pagesTotal={generation.pagesTotal}
              audioCurrent={generation.audioCurrent}
              audioTotal={generation.audioTotal}
              message={generation.message}
              lecture={lecture}
              onInterrupt={onInterruptGeneration}
              onStart={onStartLecture}
              onRetryFailed={onRetryFailed}
              onRetry={onRetryGeneration}
            />
          )}
        </div>
      </main>
    </div>
  )
}
