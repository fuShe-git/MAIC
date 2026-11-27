import { API_BASE, fetchJson, fetchSse } from './http'
import type { SseSignal } from './http'
import type { ThemeTemplate } from './theme'

/** 一个模块的页面数据（ind 与大纲/音频对应） */
export interface PageItem {
  ind: number
  context: string
  htmlpage: string
}

/** 大纲模块：title/context 一一对应（知识点标题与口播讲稿） */
export interface OutlineModule {
  ind: number
  title: string[]
  context: string[]
  /** 本页简介；旧数据可能没有该字段，展示时兜底为空 */
  summary?: string
  /** 前端标记：用户手动新增的页面（仅用于删除与重新生成判断，提交后端无害） */
  isNew?: boolean
}

/** 生成进度步骤（v4 新增 safety | quiz | confirm | retry） */
export type ProgressStep =
  | 'check'
  | 'safety'
  | 'topic'
  | 'outline'
  | 'pages'
  | 'audio'
  | 'quiz'
  | 'assemble'
  | 'done'
  | 'reply'
  | 'error'
  | 'confirm'
  | 'retry'

/**
 * 生成进度事件（SSE 单条帧）。
 * 字段为顶层平铺（与后端 _emit 的事件结构一致）：通用进度字段 +
 * 各阶段领域字段（confirm 带 catalogList/threadId，done/retry 带产物与失败清单）。
 */
export interface ProgressEvent {
  step: ProgressStep
  status: 'start' | 'progress' | 'done' | 'failed' | 'retrying'
  /** 阶段内进度：已完成数 */
  current?: number
  /** 阶段内进度：总数 */
  total?: number
  /** 展示文案 */
  message?: string
  /** 讲解主题（topic 事件携带） */
  topic?: string
  /** 逐项进度：当前模块 ind（pages/audio/retry 事件携带） */
  ind?: number
  /** done/retry：组装后的课件 HTML 落盘路径 */
  courseHtml?: string
  /** done：导出的 MP4 落盘路径 */
  videoUrl?: string
  /** done/retry：True 表示有失败页的降级交付 */
  partial?: boolean
  /** done/retry：失败清单 [{ind, stage, error}] */
  failedPages?: Array<{ ind: number; stage: string; error?: string }>
  /** done：课件资产 id */
  courseId?: string
  /** confirm/done：图线程 id（确认恢复执行用） */
  threadId?: string
  /** confirm：待确认的大纲（人工确认用，可编辑） */
  catalogList?: OutlineModule[]
}

/** POST /chat 返回体（v4 新增字段均为可选，完全兼容） */
export interface ChatResponse {
  reply: string
  needLecture: boolean
  topic: string
  courseHtml: string
  videoUrl: string
  /** 逐页 HTML（done 事件不携带，仅 POST /chat 或降级兜底时有值） */
  pageList?: PageItem[]
  /** True 表示有失败页的降级交付 */
  partial?: boolean
  /** 失败清单 [{ind, stage, error}] */
  failedPages?: Array<{ ind: number; stage: string; error?: string }>
  threadId?: string
  courseId?: string
}

/** 人工确认大纲的请求体（catalogList 传编辑后的大纲；confirmed=false 表示取消） */
export interface ConfirmRequest {
  threadId: string
  catalogList?: OutlineModule[]
  confirmed?: boolean
  runId?: string
  /** 所选模型「配置id:模型id」；不传表示使用服务端默认模型 */
  modelId?: string
  /** 用户选择的 PPT 样式；旧调用不传时后端沿用默认样式 */
  theme?: ThemeTemplate
}

/** 手动重试失败页的请求体 */
export interface RetryRequest {
  sessionId?: string
  threadId?: string
  inds?: number[]
  runId?: string
  /** 所选模型「配置id:模型id」；不传表示使用服务端默认模型 */
  modelId?: string
  /** 失败页重试仍需沿用最初选择的 PPT 样式 */
  theme?: ThemeTemplate
}

/** 前置校验：进入生成页前检查输入违规与是否有讲解主题 */
export interface PrecheckRequest {
  message: string
  sessionId?: string
  modelId?: string
}

export interface PrecheckResponse {
  ok: boolean
  /** SAFETY_BLOCKED / NO_TOPIC / NO_LECTURE / PRECHECK_ERROR */
  code: string
  message: string
  /** 归一化后的讲解主题；成功时返回 */
  topic: string
  /** 成功时下发，后续生成请求携带以跳过图内重复校验 */
  token: string
}

export interface ChatPayload {
  message: string
  sessionId?: string
  threadId?: string
  autoConfirm?: boolean
  /** 仅重生成大纲：后端跳过查询改写与检索，只做安全校验和大纲生成 */
  outlineOnly?: boolean
  /** 重生成大纲时的真实课程主题，避免把整段操作指令当成讲解主题 */
  topic?: string
  /** 重生成大纲时用户对大纲的补充要求（如新增页面） */
  outlineHint?: string
  history?: Array<{ role: 'user' | 'assistant'; content: string }>
  runId?: string
  /** 前置校验凭证；携带后图内跳过输入审核与意图分析 */
  precheckToken?: string
  /** 所选模型「配置id:模型id」；不传表示使用服务端默认模型 */
  modelId?: string
}

/** 前置校验：先判断输入是否违规、是否有需要讲解的主题（普通 JSON，不走 SSE）。 */
export function precheckInput(
  payload: PrecheckRequest,
  signal?: SseSignal,
): Promise<PrecheckResponse> {
  return fetchJson<PrecheckResponse>(`${API_BASE}/chat/precheck`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/**
 * 通知后端取消指定 runId 的生成任务（真正停止后台跑图，而非仅断开 SSE 连接）。
 * keepalive=true 保证页面刷新/关闭时请求仍能送达。
 */
export function cancelGeneration(runId: string, keepalive = false): Promise<{ cancelled: boolean }> {
  return fetchJson<{ cancelled: boolean }>(`${API_BASE}/chat/cancel`, {
    method: 'POST',
    body: JSON.stringify({ runId }),
    keepalive,
  })
}

/**
 * 一次性对话（不需要流式进度）。
 */
export function chatMessage(payload: ChatPayload, signal?: SseSignal): Promise<ChatResponse> {
  return fetchJson<ChatResponse>(`${API_BASE}/chat`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/**
 * 流式对话（SSE 进度流）：onEvent 逐条接收进度事件，完成后返回。
 */
export function chatStream(
  payload: ChatPayload,
  onEvent: (event: ProgressEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return fetchSse<ProgressEvent>(`${API_BASE}/chat/stream`, onEvent, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/**
 * 兼容旧调用：只给输入文本的流式生成。
 */
export function streamMessage(
  text: string,
  onEvent: (event: ProgressEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return chatStream({ message: text }, onEvent, signal)
}

/**
 * 人工确认大纲后恢复执行（SSE）。
 * catalogList 传编辑后的大纲继续生成；confirmed=false 表示取消。
 */
export function confirmOutline(
  request: ConfirmRequest,
  onEvent: (event: ProgressEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return fetchSse<ProgressEvent>(`${API_BASE}/chat/confirm`, onEvent, {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  })
}

/** 用最终确认的大纲与样式生成课件（每次新开线程，不复用旧线程状态） */
export interface GenerateCourseRequest {
  topic: string
  catalogList?: OutlineModule[]
  theme?: ThemeTemplate
  sessionId?: string
  runId?: string
  modelId?: string
}

/**
 * 生成课件（SSE）：后端新开图线程，跳过大纲/检索，直接产出页面与音频。
 * 停止生成后再次生成必须走这里，避免恢复旧线程带回上一次的产物。
 */
export function generateCourse(
  request: GenerateCourseRequest,
  onEvent: (event: ProgressEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return fetchSse<ProgressEvent>(`${API_BASE}/chat/generate`, onEvent, {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  })
}

/**
 * 手动重试失败页（SSE）。失败清单与重试进度以事件推送。
 */
export function retryFailedPages(
  request: RetryRequest,
  onEvent: (event: ProgressEvent) => void,
  signal?: SseSignal,
): Promise<void> {
  return fetchSse<ProgressEvent>(`${API_BASE}/chat/retry`, onEvent, {
    method: 'POST',
    body: JSON.stringify(request),
    signal,
  })
}

/** 把框架英文异常转成中文提示，避免把 LangGraph 报错原文直接展示给用户 */
export function friendlyErrorMessage(message: string, fallback = '生成出错，请重试；若仍失败，请联系管理员。'): string {
  if (/recursion limit of \d+ reached|GRAPH_RECURSION_LIMIT/i.test(message)) {
    return fallback
  }
  return message
}
