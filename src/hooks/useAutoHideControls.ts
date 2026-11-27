import { useCallback, useEffect, useRef, useState } from 'react'

/** 鼠标活动节流间隔：避免每次 mousemove 都重建定时器 */
const POKE_THROTTLE_MS = 200

/**
 * 全屏浮层控制条的显隐：
 * - active 变为 true（进入全屏）时立即显示，并开始计时；
 * - 静止超过 delay 毫秒自动隐藏；
 * - poke() 用于鼠标移动 / 按键 / 触摸时重新显示并重置计时；
 * - hold()/release() 用于鼠标悬停在浮层上（含倍速菜单）时保持不隐藏。
 */
export function useAutoHideControls(active: boolean, delay = 2600) {
  const [visible, setVisible] = useState(active)
  const timerRef = useRef<number | null>(null)
  /** 悬停等「必须常驻」状态，用 ref 避免把定时器逻辑挂到 state 上 */
  const holdRef = useRef(false)
  const lastPokeRef = useRef(0)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const schedule = useCallback(() => {
    clearTimer()
    if (holdRef.current) return
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      setVisible(false)
    }, delay)
  }, [clearTimer, delay])

  const poke = useCallback(() => {
    const now = Date.now()
    if (now - lastPokeRef.current < POKE_THROTTLE_MS) return
    lastPokeRef.current = now
    setVisible(true)
    schedule()
  }, [schedule])

  const hold = useCallback(() => {
    holdRef.current = true
    clearTimer()
    setVisible(true)
  }, [clearTimer])

  const release = useCallback(() => {
    holdRef.current = false
    schedule()
  }, [schedule])

  // active 变化时用渲染期比较同步 visible，effect 只负责定时器与 ref
  const [prevActive, setPrevActive] = useState(active)
  if (active !== prevActive) {
    setPrevActive(active)
    setVisible(active)
  }

  useEffect(() => {
    if (!active) {
      clearTimer()
      holdRef.current = false
      return
    }
    schedule()
    return clearTimer
  }, [active, schedule, clearTimer])

  return { visible, poke, hold, release }
}
