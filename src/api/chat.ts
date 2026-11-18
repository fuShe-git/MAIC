export interface ChatResponse {
  reply: string
}

/**
 * 调用后端对话接口。
 * 开发环境由 Vite 代理转发到 http://127.0.0.1:8001/api/chat
 */
export async function sendMessage(
  message: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })

  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const data: { detail?: string } = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      // 响应体不是 JSON 时忽略，使用默认提示
    }
    throw new Error(detail)
  }

  const data = (await response.json()) as ChatResponse
  return data.reply
}
