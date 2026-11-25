import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { I18nContext } from './context'
import { MESSAGES } from './messages'
import type { Lang, MessageKey } from './messages'

const STORAGE_KEY = 'maic.lang'
/** 语言切换节流间隔：3 秒内只允许切换一次 */
const TOGGLE_INTERVAL_MS = 3000

/** 读取本地保存的语种，非法值回落到中文 */
function readStoredLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh' || saved === 'en') return saved
  } catch {
    // 隐私模式等场景读不到 localStorage：用默认值
  }
  return 'zh'
}

/** 把 {name} 形式的占位符替换为实际值 */
function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  )
}

export default function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang)
  // 节流时间戳：只拦 toggleLang，setLang 的显式指定不受影响
  const lastToggleRef = useRef(0)

  // 同步 <html lang>，方便字体回退与无障碍朗读
  useEffect(() => {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
  }, [lang])

  const setLang = useCallback((next: Lang) => {
    setLangState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 写不进去不影响当前会话
    }
  }, [])

  const toggleLang = useCallback(() => {
    // 3 秒内重复点击直接忽略；首次点击 lastToggleRef 为 0，立即可用
    const now = Date.now()
    if (now - lastToggleRef.current < TOGGLE_INTERVAL_MS) return false
    lastToggleRef.current = now
    setLangState((prev) => {
      const next: Lang = prev === 'zh' ? 'en' : 'zh'
      try {
        localStorage.setItem(STORAGE_KEY, next)
      } catch {
        // 同上
      }
      return next
    })
    return true
  }, [])

  const t = useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) =>
      interpolate(MESSAGES[lang][key] ?? MESSAGES.zh[key] ?? key, vars),
    [lang],
  )

  const value = useMemo(() => ({ lang, setLang, toggleLang, t }), [lang, setLang, toggleLang, t])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
