import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useI18n } from '../i18n/context'
import { useTheme } from '../theme/context'
import type { ThemeMode } from '../theme/context'
import EmbeddingSettings from './EmbeddingSettings'
import LlmSettings from './LlmSettings'
import './SettingsModal.css'

type SectionKey = 'theme' | 'llm' | 'embedding'

/** 左侧导航图标（线性风格，随文字颜色变化） */
const SECTION_ICONS: Record<SectionKey, ReactNode> = {
  theme: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3a9 9 0 1 0 0 18c1.3 0 2-.9 2-2 0-.6-.2-1-.6-1.5-.3-.4-.5-.8-.5-1.3 0-1 .8-1.7 1.8-1.7H16a5 5 0 0 0 5-5c0-3.9-4-6.5-9-6.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="10" r="1.1" fill="currentColor" />
      <circle cx="12" cy="7.5" r="1.1" fill="currentColor" />
      <circle cx="16" cy="10" r="1.1" fill="currentColor" />
    </svg>
  ),
  llm: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M7.5 17.5h9a4 4 0 0 0 .6-7.96A5.5 5.5 0 0 0 6.9 8.6 4.5 4.5 0 0 0 7.5 17.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  ),
  embedding: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  ),
}

const THEME_ICONS: Record<ThemeMode, ReactNode> = {
  light: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4m0-12.8-1.4 1.4m-10 10L5.6 18.4"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  ),
  dark: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 14.5A8.2 8.2 0 0 1 9.5 4a8.4 8.4 0 1 0 10.5 10.5Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  ),
  system: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="4.5" width="18" height="12" rx="2" stroke="currentColor" strokeWidth="1.7" />
      <path d="M9 20h6M12 16.5V20" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  ),
}

interface SettingsModalProps {
  open: boolean
  onClose: () => void
  /** 大模型配置新增 / 编辑 / 删除成功后通知外层刷新模型下拉 */
  onLlmConfigsChanged?: () => void
}

/** 设置弹窗：左侧模块导航 + 右侧内容面板 */
export default function SettingsModal({ open, onClose, onLlmConfigsChanged }: SettingsModalProps) {
  const { t } = useI18n()
  const { mode, setMode } = useTheme()
  const [section, setSection] = useState<SectionKey>('theme')
  const [llmFormOpen, setLlmFormOpen] = useState(false)

  const sections: Array<{ key: SectionKey; label: string }> = [
    { key: 'theme', label: t('settings.nav.theme') },
    { key: 'llm', label: t('settings.nav.llm') },
    { key: 'embedding', label: t('settings.nav.embedding') },
  ]

  const themeOptions: Array<{ value: ThemeMode; label: string }> = [
    { value: 'light', label: t('theme.light') },
    { value: 'dark', label: t('theme.dark') },
    { value: 'system', label: t('theme.system') },
  ]

  const current = sections.find((item) => item.key === section) ?? sections[0]

  // 关闭时重置大模型表单状态，避免下次打开停留在编辑态
  // 渲染期比较代替 effect 内同步 setState
  const [prevOpen, setPrevOpen] = useState(open)
  if (prevOpen !== open) {
    setPrevOpen(open)
    if (!open) setLlmFormOpen(false)
  }

  // 打开时锁定背景滚动，并支持 Esc 关闭（表单打开时先关表单）
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (section === 'llm' && llmFormOpen) {
        setLlmFormOpen(false)
        return
      }
      onClose()
    }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = prevOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose, section, llmFormOpen])

  if (!open) return null

  return (
    <div
      className="settings-mask"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (section === 'llm' && llmFormOpen) setLlmFormOpen(false)
        else onClose()
      }}
    >
      <div className="settings-modal" role="dialog" aria-modal="true" aria-label={t('settings.title')}>
        <aside className="settings-nav">
          <div className="settings-nav-title">{t('settings.title')}</div>
          <nav className="settings-nav-list">
            {sections.map((item) => (
              <button
                key={item.key}
                className={`settings-nav-item ${item.key === section ? 'is-active' : ''}`}
                type="button"
                onClick={() => {
                  setSection(item.key)
                  setLlmFormOpen(false)
                }}
              >
                <span className="settings-nav-icon">{SECTION_ICONS[item.key]}</span>
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        <section className="settings-panel">
          <header className="settings-panel-head">
            <h2 className="settings-panel-title">{current.label}</h2>
            <div className="settings-panel-actions">
              {section === 'llm' && !llmFormOpen && (
                <button className="settings-add-btn" type="button" onClick={() => setLlmFormOpen(true)}>
                  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  {t('settings.llm.add')}
                </button>
              )}
              <button
                className="settings-close"
                type="button"
                onClick={onClose}
                aria-label={t('common.close')}
                title={t('common.close')}
              >
                <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M6 6l12 12M18 6 6 18"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>
          </header>

          {section === 'llm' ? (
            <LlmSettings
              formOpen={llmFormOpen}
              onFormOpenChange={setLlmFormOpen}
              onConfigsChanged={onLlmConfigsChanged}
            />
          ) : section === 'embedding' ? (
            <EmbeddingSettings />
          ) : section === 'theme' ? (
            <div className="settings-theme">
              <p className="settings-theme-desc">{t('settings.theme.desc')}</p>
              <div className="settings-theme-options">
                {themeOptions.map((option) => (
                  <button
                    key={option.value}
                    className={`settings-theme-option ${mode === option.value ? 'is-active' : ''}`}
                    type="button"
                    onClick={() => setMode(option.value)}
                  >
                    <span className="settings-theme-icon">{THEME_ICONS[option.value]}</span>
                    <span>{option.label}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  )
}
