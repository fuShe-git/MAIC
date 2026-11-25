import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { exportGeneratedPptx } from '../api/generated'
import { useI18n } from '../i18n/context'
import { useTheme } from '../theme/context'
import type { ThemeMode } from '../theme/context'
import { downloadBlob, withMinDuration } from '../utils/download'
import DownloadIcon from './DownloadIcon'
import SpinnerIcon from './SpinnerIcon'
import './TopBar.css'

interface PlayerTopActionsProps {
  /** 课件资产 id（32 位 hex，等于落盘 HTML 文件名 stem） */
  courseId: string
  /** 课件标题，用作下载文件名 */
  topic: string
}

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

/** 课件播放页顶栏右侧操作区：中英切换 / 主题切换 / 导出 PPT */
export default function PlayerTopActions({ courseId, topic }: PlayerTopActionsProps) {
  const { lang, toggleLang, t } = useI18n()
  const { mode, resolved, setMode } = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [toast, setToast] = useState('')
  // 状态更新是异步的，用 ref 挡住同一帧内的重复点击
  const exportingRef = useRef(false)
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

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const THEME_OPTIONS: Array<{ value: ThemeMode; label: string; icon: ReactNode }> = [
    { value: 'light', label: t('theme.light'), icon: <SunIcon /> },
    { value: 'dark', label: t('theme.dark'), icon: <MoonIcon /> },
    { value: 'system', label: t('theme.system'), icon: <MonitorIcon /> },
  ]

  /** 切换语言：被 3 秒节流拦截时给出提示 */
  function handleToggleLang() {
    if (!toggleLang()) setToast(t('topbar.langThrottled'))
  }

  async function handleExport() {
    if (exportingRef.current || !courseId) return
    exportingRef.current = true
    setExporting(true)
    try {
      // 缓存命中时请求瞬间返回，包一层最小时长保证加载图标可见
      const blob = await withMinDuration(exportGeneratedPptx(`${courseId}.html`), 600)
      // 文件名用课件标题并去掉文件系统非法字符，空名时回退课件 id
      const baseName =
        topic
          .replace(/[\\/:*?"<>|]/g, ' ')
          .replace(/\s+/g, ' ')
          .trim() || courseId
      downloadBlob(blob, `${baseName}.pptx`)
    } catch {
      setToast(t('history.exportPptFailed'))
    } finally {
      exportingRef.current = false
      setExporting(false)
    }
  }

  return (
    <div className="lp-top-actions" ref={rootRef}>
      <div className="topbar-group">
        <button
          className="topbar-btn topbar-lang"
          type="button"
          onClick={handleToggleLang}
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
          title={exporting ? t('history.exporting') : t('history.exportPpt')}
          aria-label={t('history.exportPpt')}
          aria-busy={exporting}
          disabled={exporting || !courseId}
          onClick={handleExport}
        >
          {exporting ? <SpinnerIcon /> : <DownloadIcon />}
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

      {toast ? (
        <div className="lp-action-toast" role="status">
          {toast}
        </div>
      ) : null}
    </div>
  )
}
