import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'

/** 向量化配置：表单里数字统一用字符串承载，提交时再转成数字 */
export interface EmbeddingConfig {
  baseUrl: string
  model: string
  dimensions: string
  timeout: string
  apiKey: string
  isDefault: boolean
  updatedAt: string
}

/** 提交给后端的配置；apiKey 留空表示保留数据库里的原密钥 */
export interface EmbeddingConfigInput {
  baseUrl: string
  model: string
  dimensions: number
  timeout: number
  apiKey: string
}

export interface EmbeddingTestResult {
  ok: boolean
  dimensions: number
  message: string
}

interface RawEmbeddingConfig {
  baseUrl?: string
  model?: string
  dimensions?: number | string
  timeout?: number | string
  apiKey?: string
  isDefault?: boolean
  updatedAt?: string
}

function normalize(raw: RawEmbeddingConfig): EmbeddingConfig {
  return {
    baseUrl: String(raw.baseUrl ?? ''),
    model: String(raw.model ?? ''),
    dimensions: String(raw.dimensions ?? 1024),
    timeout: String(raw.timeout ?? 120),
    apiKey: String(raw.apiKey ?? ''),
    isDefault: Boolean(raw.isDefault),
    updatedAt: String(raw.updatedAt ?? ''),
  }
}

/** 读取唯一配置（密钥已由后端脱敏） */
export async function getEmbeddingConfig(signal?: SseSignal): Promise<EmbeddingConfig> {
  return normalize(await fetchJson<RawEmbeddingConfig>(`${API_BASE}/embedding-config`, { signal }))
}

/** 新增 / 覆盖唯一配置 */
export async function saveEmbeddingConfig(input: EmbeddingConfigInput): Promise<EmbeddingConfig> {
  return normalize(
    await fetchJson<RawEmbeddingConfig>(`${API_BASE}/embedding-config`, {
      method: 'PUT',
      body: JSON.stringify(input),
    }),
  )
}

/** 用当前表单参数测试向量接口连通性 */
export async function testEmbeddingConfig(input: EmbeddingConfigInput): Promise<EmbeddingTestResult> {
  return fetchJson<EmbeddingTestResult>(`${API_BASE}/embedding-config/test`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
