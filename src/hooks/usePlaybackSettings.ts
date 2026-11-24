import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { CourseSourceKind } from '../utils/courseSource'
import {
  applyCourseSettings,
  applyVideoSettings,
  readStoredVolume,
  storeVolume,
} from '../utils/coursewareControl'

/** 自动播放 / 倍速 / 音量，音量持久化；同时把设置下发给课件或视频 */
export function usePlaybackSettings(
  frameRef: RefObject<HTMLIFrameElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  frameReady: boolean,
  kind: CourseSourceKind,
) {
  const [autoPlay, setAutoPlay] = useState(true)
  const [rate, setRate] = useState(1)
  const [volume, setVolume] = useState(readStoredVolume)
  const apiAvailableRef = useRef(false)

  useEffect(() => {
    const settings = { autoPlay, rate, volume }
    apiAvailableRef.current =
      kind === 'html' && frameReady ? applyCourseSettings(frameRef.current, settings) : false
    if (kind === 'video') applyVideoSettings(videoRef.current, settings)
  }, [autoPlay, rate, volume, frameReady, kind, frameRef, videoRef])

  useEffect(() => {
    storeVolume(volume)
  }, [volume])

  const toggleAutoPlay = useCallback(() => setAutoPlay((value) => !value), [])

  return { autoPlay, toggleAutoPlay, rate, setRate, volume, setVolume, apiAvailableRef }
}

/** 课件卡片全屏；Esc 由浏览器原生退出，这里只同步按钮状态 */
export function useFullscreen(targetRef: RefObject<HTMLElement | null>) {
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    const onChange = () => setIsFullscreen(document.fullscreenElement === targetRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [targetRef])

  const toggle = useCallback(() => {
    const element = targetRef.current
    if (!element || typeof element.requestFullscreen !== 'function') return
    if (document.fullscreenElement === element) {
      void document.exitFullscreen().catch((error: unknown) => console.error('退出全屏失败', error))
      return
    }
    void element.requestFullscreen().catch((error: unknown) => console.error('进入全屏失败', error))
  }, [targetRef])

  return { isFullscreen, toggle }
}
