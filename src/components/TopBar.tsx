import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useI18n } from '../i18n/context'
import { useTheme } from '../theme/context'
import type { ThemeMode } from '../theme/context'
import './TopBar.css'

/** 太阳：浅色主题 */
function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4m0-12.8-1.4 1.4m-10 10L5.6 18.4"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  )
}

/** 月亮：深色主题 */
function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 14.5A8.2 8.2 0 0 1 9.5 4a8.4 8.4 0 1 0 10.5 10.5Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** 显示器：跟随系统 */
function MonitorIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="4.5" width="18" height="12" rx="2" stroke="currentColor" strokeWidth="1.7" />
      <path d="M9 20h6M12 16.5V20" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

/** 齿轮：设置 */
function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="3.4" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M19.5 12c0-.5-.05-.98-.14-1.45l1.86-1.4-1.9-3.3-2.2.86a7.9 7.9 0 0 0-2.57-1.42L14.1 2.9h-4.2l-.45 2.4a7.9 7.9 0 0 0-2.57 1.42l-2.2-.86-1.9 3.3 1.86 1.4a8 8 0 0 0 0 2.9l-1.86 1.4 1.9 3.3 2.2-.86c.75.64 1.62 1.13 2.57 1.42l.45 2.4h4.2l.45-2.4a7.9 7.9 0 0 0 2.57-1.42l2.2.86 1.9-3.3-1.86-1.4c.09-.47.14-.95.14-1.45Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  )
}

interface TopBarProps {
  /** 打开设置弹窗 */
  onOpenSettings: () => void
}

/** 首页右上角工具栏：语言切换 / 主题下拉 / 设置入口 */
export default function TopBar({ onOpenSettings }: TopBarProps) {
  const { lang, toggleLang, t } = useI18n()
  const { mode, resolved, setMode } = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  // 点击外部或按 Esc 收起主题下拉
  useEffect(() => {
    if (!menuOpen) return
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setMenuOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menuOpen])

  const THEME_OPTIONS: Array<{ value: ThemeMode; label: string; icon: ReactNode }> = [
    { value: 'light', label: t('theme.light'), icon: <SunIcon /> },
    { value: 'dark', label: t('theme.dark'), icon: <MoonIcon /> },
    { value: 'system', label: t('theme.system'), icon: <MonitorIcon /> },
  ]

  return (
    <div className="topbar" ref={rootRef}>
      <div className="topbar-group">
        <button
          className="topbar-btn topbar-lang"
          type="button"
          onClick={toggleLang}
          title={t('topbar.lang')}
          aria-label={t('topbar.lang')}
        >
          <span className="topbar-lang-text">{lang === 'zh' ? '中' : 'EN'}</span>
        </button>
        <span className="topbar-divider" aria-hidden="true" />
        <button
          className={`topbar-btn ${menuOpen ? 'is-open' : ''}`}
          type="button"
          onClick={() => setMenuOpen((prev) => !prev)}
          title={t('topbar.theme')}
          aria-label={t('topbar.theme')}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          {resolved === 'dark' ? <MoonIcon /> : <SunIcon />}
        </button>
        <span className="topbar-divider" aria-hidden="true" />
        <button
          className="topbar-btn"
          type="button"
          onClick={onOpenSettings}
          title={t('topbar.settings')}
          aria-label={t('topbar.settings')}
        >
          <GearIcon />
        </button>
      </div>

      {menuOpen ? (
        <div className="theme-menu" role="menu" aria-label={t('topbar.theme')}>
          {THEME_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`theme-menu-item ${mode === option.value ? 'is-active' : ''}`}
              type="button"
              role="menuitemradio"
              aria-checked={mode === option.value}
              onClick={() => {
                setMode(option.value)
                setMenuOpen(false)
              }}
            >
              <span className="theme-menu-icon">{option.icon}</span>
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
