import { createContext, useContext } from 'react'

/** 主题偏好：跟随系统时由系统配色决定实际主题 */
export type ThemeMode = 'light' | 'dark' | 'system'

/** 实际生效的主题（system 已被解析） */
export type ResolvedTheme = 'light' | 'dark'

export interface ThemeContextValue {
  /** 用户选择的主题偏好 */
  mode: ThemeMode
  /** 当前实际生效的主题 */
  resolved: ResolvedTheme
  /** 修改主题偏好（会持久化到 localStorage） */
  setMode: (mode: ThemeMode) => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

/** 读取主题上下文；必须在 ThemeProvider 内部使用 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme 必须在 ThemeProvider 内部使用')
  return ctx
}
