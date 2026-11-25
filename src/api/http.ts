/**
 * HTTP 基础层：统一 /api 前缀、JSON 请求与 SSE（Server-Sent Events，服务端单向推流）解析。
 * 项目不引入第三方 SSE 依赖，POST + fetch 流式读取手写解析。
 */

export const API_BASE = '/api'

export type SseSignal = AbortSignal | undefined

export interface SseOptions {
  method?: string
  body?: string
  signal?: SseSignal
  /** 页面卸载时仍保证请求发出（用于取消后台生成任务） */
  keepalive?: boolean
}

/** 二进制下载请求：非 2xx 复用 JSON 错误解析，成功返回 Blob */
export async function fetchBlob(url: string, signal?: SseSignal): Promise<Blob> {
  const res = await fetch(url, { method: 'GET', signal })
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      // 忽略解析失败，用默认文案
    }
    throw new Error(detail)
  }
  return await res.blob()
}

/** JSON 请求：非 2xx 抛错，返回解析后的 JSON */
export async function fetchJson<T>(url: string, options: SseOptions = {}): Promise<T> {
  const res = await fetch(url, {
    method: options.method ?? 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: options.body,
    signal: options.signal,
    keepalive: options.keepalive,
  })
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      // 忽略解析失败，用默认文案
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

/**
 * SSE 流式请求（POST + 流式读取）：逐帧解析 `data: {...}` 并回调 onEvent。
 * 流结束或出错时返回；主动 abort 由上层通过 signal 控制。
 */
export async function fetchSse<T>(
  url: string,
  onEvent: (event: T) => void,
  options: SseOptions = {},
): Promise<void> {
  const res = await fetch(url, {
    method: options.method ?? 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: options.body,
    signal: options.signal,
  })
  if (!res.ok || !res.body) {
    let detail = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      if (data?.detail) detail = String(data.detail)
    } catch {
      // 忽略解析失败，用默认文案
    }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const flush = (frame: string) => {
    // 逐行找 `data:` 前缀的行拼成一条事件
    for (const line of frame.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) continue
      const payload = trimmed.slice(5).trim()
      if (!payload) continue
      try {
        onEvent(JSON.parse(payload) as T)
      } catch {
        // 半截帧/非法 JSON 直接忽略
      }
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 以空行分帧
    let sep = buffer.indexOf('\n\n')
    while (sep >= 0) {
      flush(buffer.slice(0, sep))
      buffer = buffer.slice(sep + 2)
      sep = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) flush(buffer)
}
