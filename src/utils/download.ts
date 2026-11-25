/** 用临时 object URL 触发浏览器下载；调用方负责取回 Blob */
export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 立即 revoke 可能打断部分浏览器的下载，延迟释放
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * 等待任务完成，并保证至少 minMs 毫秒。
 * 缓存命中时请求会瞬间返回，React 会把 loading 的开/关合并成一帧，
 * 导致加载图标根本看不见；包一层最小时长后 loading 一定能被看到。
 * 任务失败时同样会等够时长再抛出，由调用方统一提示。
 */
export async function withMinDuration<T>(task: Promise<T>, minMs: number): Promise<T> {
  const [result] = await Promise.all([
    task.then(
      (value) => ({ ok: true as const, value }),
      (error: unknown) => ({ ok: false as const, error }),
    ),
    new Promise<void>((resolve) => window.setTimeout(resolve, minMs)),
  ])
  if (!result.ok) throw result.error
  return result.value
}
