import { useState } from 'react'
import type { ThemeTemplate } from '../api/theme'
import GeneratingMark from './GeneratingMark'
import { useI18n } from '../i18n/context'
import './ThemeSelectPanel.css'

export type ThemePanelStatus = 'loading' | 'ready' | 'regenerating' | 'error'

interface ThemeSelectPanelProps {
  themes: ThemeTemplate[]
  status: ThemePanelStatus
  errorMessage?: string
  selectedThemeId: string
  canStart: boolean
  startDisabledReason: string
  onSelect: (themeId: string) => void
  onRegenerateTheme: (feedback: string) => void
  onStart: () => void
}

/** PPT 样式选择面板：3 个候选模板，支持定向修改意见与重新生成。 */
export default function ThemeSelectPanel({
  themes,
  status,
  errorMessage,
  selectedThemeId,
  canStart,
  startDisabledReason,
  onSelect,
  onRegenerateTheme,
  onStart,
}: ThemeSelectPanelProps) {
  const { t } = useI18n()
  const [feedback, setFeedback] = useState('')
  const busy = status === 'loading' || status === 'regenerating'
  const canRegenerate = feedback.trim().length > 0 && !busy

  return (
    <section className="pw-fade-in flex min-h-0 flex-1 flex-col gap-3">
      <header className="shrink-0">
        <p className="m-0 text-[13px] leading-5 text-[var(--text-dim)]">{t('themeSelect.desc')}</p>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1 pw-scroll">
        {busy && themes.length === 0 ? (
          <GeneratingMark
            title={t('themeSelect.generatingTitle')}
            subtitle={t('themeSelect.generatingSubtitle')}
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {themes.map((theme, index) => {
              const selected = selectedThemeId === theme.id
              return (
                <button
                  key={theme.id}
                  type="button"
                  onClick={() => onSelect(theme.id)}
                  className={`pw-theme-card group relative flex h-full cursor-pointer flex-col rounded-2xl border bg-[var(--bg)] p-4 text-left transition-all duration-200 ${
                    selected
                      ? 'border-[#7259E8] ring-2 ring-[#7259E8]/25 shadow-[0_18px_40px_-22px_rgba(91,69,224,0.75)]'
                      : 'border-[var(--border)] hover:-translate-y-0.5 hover:border-[#B6ADFF] hover:shadow-[0_16px_36px_-24px_rgba(40,32,90,0.6)]'
                  }`}
                >
                  <div className="mb-3 flex items-center justify-between">
                    <span className="text-[12px] font-semibold text-[var(--text-dim)]">
                      {t('themeSelect.item', { n: index + 1 })}
                    </span>
                    {selected ? (
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#5B45E0] text-[11px] text-white">
                        ✓
                      </span>
                    ) : null}
                  </div>

                  <div
                    className="mb-3 flex h-20 items-end gap-1 overflow-hidden rounded-xl border border-white/60 p-2 shadow-inner"
                    style={{ background: theme.previewColors.background }}
                  >
                    <span
                      className="h-9 w-2/5 rounded-md"
                      style={{ background: theme.previewColors.primary }}
                    />
                    <span className="flex flex-1 flex-col gap-1.5">
                      <span
                        className="h-2 rounded-full opacity-70"
                        style={{ background: theme.previewColors.text }}
                      />
                      <span
                        className="h-2 w-4/5 rounded-full opacity-40"
                        style={{ background: theme.previewColors.text }}
                      />
                      <span
                        className="mt-auto h-3 rounded-full"
                        style={{ background: theme.previewColors.accent }}
                      />
                    </span>
                  </div>

                  <h3 className="m-0 text-[15px] font-semibold text-[var(--text)]">{theme.name}</h3>
                  <p className="mt-1 text-[12px] leading-5 text-[var(--text-dim)]">{theme.description}</p>
                  <p className="mt-2 line-clamp-3 text-[11px] leading-5 text-[var(--text-faint)]">
                    {theme.stylePrompt}
                  </p>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {errorMessage ? (
        <p className="m-0 shrink-0 rounded-xl bg-[#FDECEC] px-3 py-2 text-[12px] text-[#B3261E]">
          {errorMessage}
        </p>
      ) : null}

      {!busy ? (
        <div className="shrink-0 rounded-2xl border border-[var(--border)] bg-[var(--surface-hover)] p-3">
          <label className="block text-[12px] font-medium text-[var(--text-dim)]">
            {t('themeSelect.feedback')}
            <input
              className="mt-1 w-full rounded-xl border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-[13px] text-[var(--text)] outline-none transition focus:border-[#7259E8] focus:ring-2 focus:ring-[#7259E8]/20"
              value={feedback}
              maxLength={300}
              placeholder={t('themeSelect.feedbackPlaceholder')}
              onChange={(event) => setFeedback(event.target.value)}
            />
          </label>

          <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              className="cursor-pointer rounded-xl border border-[#DAD4FF] bg-[#F5F2FF] px-3 py-2 text-[13px] font-medium text-[#5B45E0] transition hover:bg-[#EBE5FF] disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onRegenerateTheme(feedback.trim())}
              disabled={!canRegenerate}
            >
              {t('themeSelect.regenerate')}
            </button>
            {/* 用外层 span 承载 title，保证按钮禁用时也能看到 hover 提示 */}
            <span
              className="inline-flex"
              title={canStart ? t('prepare.readyToGenerate') : startDisabledReason}
            >
              <button
                type="button"
                className="cursor-pointer rounded-xl bg-gradient-to-r from-[#5B45E0] to-[#8B7BFF] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_10px_24px_-12px_rgba(91,69,224,0.9)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={onStart}
                disabled={!canStart}
              >
                {t('themeSelect.start')}
              </button>
            </span>
          </div>
          {!canStart ? (
            <p className="mb-0 mt-2 text-[11px] text-[var(--text-faint)]">{startDisabledReason}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
