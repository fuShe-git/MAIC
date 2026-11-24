import { API_BASE, fetchJson } from './http'
import type { SseSignal } from './http'

/** 单个模型：id 由前端生成，name 是调用接口时真正使用的模型名 */
export interface ModelItem {
  id: string
  name: string
}

/** 服务商配置（后端返回；apiKey 已脱敏） */
export interface LlmConfig {
  id: string
  name: string
  timeout: string
  baseUrl: string
  apiKey: string
  models: ModelItem[]
}

/** 新建 / 更新配置的请求体（apiKey 留空表示更新时保留原密钥） */
export interface LlmConfigInput {
  name: string
  timeout: string
  baseUrl: string
  apiKey: string
  models: ModelItem[]
}

interface RawLlmConfig {
  id?: string
  name?: string
  timeout?: number | string
  baseUrl?: string
  apiKey?: string
  models?: Array<{ id?: string; name?: string }>
}

interface LlmConfigList {
  items?: RawLlmConfig[]
}

/** 后端 timeout 是数字，前端表单统一用字符串，这里做一次归一化 */
function normalizeConfig(raw: RawLlmConfig): LlmConfig {
  return {
    id: String(raw.id ?? ''),
    name: String(raw.name ?? ''),
    timeout: raw.timeout === undefined || raw.timeout === null ? '120' : String(raw.timeout),
    baseUrl: String(raw.baseUrl ?? ''),
    apiKey: String(raw.apiKey ?? ''),
    models: Array.isArray(raw.models)
      ? raw.models.map((model) => ({ id: String(model.id ?? ''), name: String(model.name ?? '') }))
      : [],
  }
}

/** 列出全部服务商配置（密钥已由后端脱敏） */
export async function listLlmConfigs(signal?: SseSignal): Promise<LlmConfig[]> {
  const data = await fetchJson<LlmConfigList>(`${API_BASE}/llm-configs`, { signal })
  return (data.items ?? []).map(normalizeConfig)
}

/** 新建服务商配置（首次创建必须提供 API Key） */
export async function createLlmConfig(input: LlmConfigInput): Promise<LlmConfig> {
  const data = await fetchJson<RawLlmConfig>(`${API_BASE}/llm-configs`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return normalizeConfig(data)
}

/** 更新服务商配置；apiKey 传空串表示保留数据库里的原密钥 */
export async function updateLlmConfig(id: string, input: LlmConfigInput): Promise<LlmConfig> {
  const data = await fetchJson<RawLlmConfig>(`${API_BASE}/llm-configs/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(input),
  })
  return normalizeConfig(data)
}

/** 删除服务商配置及其模型 */
export async function deleteLlmConfig(id: string): Promise<void> {
  await fetchJson<{ deleted?: boolean }>(`${API_BASE}/llm-configs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}
