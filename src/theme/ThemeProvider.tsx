import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeContext } from './context'
import type { ResolvedTheme, ThemeMode } from './context'

const STORAGE_KEY = 'maic.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 读取本地保存的主题偏好，非法值回落到跟随系统 */
function readStoredMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  } catch {
    // 隐私模式等场景读不到 localStorage：用默认值
  }
  return 'system'
}

/** 系统当前是否为深色 */
function prefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia(DARK_QUERY).matches
}

export default function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode)
  const [systemDark, setSystemDark] = useState(prefersDark)

  // 跟随系统时监听系统配色变化
  useEffect(() => {
    const mql = window.matchMedia(DARK_QUERY)
    const listener = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    mql.addEventListener('change', listener)
    return () => mql.removeEventListener('change', listener)
  }, [])

  const resolved: ResolvedTheme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  // 主题落到 <html data-theme>，CSS 用属性选择器切换变量
  useEffect(() => {
    document.documentElement.dataset.theme = resolved
  }, [resolved])

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 写不进去不影响当前会话
    }
  }, [])

  const value = useMemo(() => ({ mode, resolved, setMode }), [mode, resolved, setMode])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
