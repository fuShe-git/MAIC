import { createContext, useContext } from 'react'
import type { Lang, MessageKey } from './messages'

export interface I18nContextValue {
  /** 当前语种 */
  lang: Lang
  /** 切换语种（会持久化到 localStorage） */
  setLang: (lang: Lang) => void
  /** 在中/英之间来回切换；返回 false 表示被 3 秒节流拦截 */
  toggleLang: () => boolean
  /** 取词条，支持 {name} 占位符插值 */
  t: (key: MessageKey, vars?: Record<string, string | number>) => string
}

export const I18nContext = createContext<I18nContextValue | null>(null)

/** 读取 i18n 上下文；必须在 I18nProvider 内部使用 */
export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n 必须在 I18nProvider 内部使用')
  return ctx
}
