import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n/context'
import {
  createLlmConfig,
  deleteLlmConfig,
  listLlmConfigs,
  updateLlmConfig,
} from '../api/llmConfigs'
import type { LlmConfig, LlmConfigInput, ModelItem } from '../api/llmConfigs'

/** 旧版本存放在 localStorage 里的配置键：改为数据库存储后按用户要求直接清理 */
const LEGACY_STORAGE_KEY = 'maic.llm.configs.v1'
const DEFAULT_TIMEOUT = '120'
/** 与后端 _TIMEOUT_MAX 保持一致 */
const MAX_TIMEOUT = 3600

interface LlmSettingsProps {
  formOpen: boolean
  onFormOpenChange: (open: boolean) => void
  /** 配置新增 / 编辑 / 删除成功后通知外层刷新模型下拉 */
  onConfigsChanged?: () => void
}

let idSeed = 0
function nextId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  idSeed += 1
  return `llm-${Date.now()}-${idSeed}`
}

function createModel(): ModelItem {
  return { id: nextId(), name: '' }
}

function createDraft(): LlmConfig {
  return {
    id: nextId(),
    name: '',
    timeout: DEFAULT_TIMEOUT,
    baseUrl: '',
    apiKey: '',
    models: [createModel()],
  }
}

/** 基础 URL 校验：必须是带 http/https 协议的完整地址 */
function isValidHttpUrl(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return false
  try {
    const url = new URL(trimmed)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

const PlusIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
)

const CloseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
)

const TrashIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M4 7h16M9 7V4.8h6V7m-9 0 1 12.2h10L18 7M10 11v5m4-5v5"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
)

const EyeIcon = ({ hidden }: { hidden: boolean }) => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M2.8 12S6.4 6.5 12 6.5s9.2 5.5 9.2 5.5-3.6 5.5-9.2 5.5S2.8 12 2.8 12Z"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
    />
    <circle cx="12" cy="12" r="2.5" stroke="currentColor" strokeWidth="1.6" />
    {hidden && <path d="M4 20 20 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />}
  </svg>
)

const EmptyIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
    <rect x="11" y="8" width="26" height="32" rx="5" stroke="currentColor" strokeWidth="2" />
    <path d="M11 17h26M18 13h1m4 0h1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <rect x="17" y="23" width="14" height="11" rx="3" stroke="currentColor" strokeWidth="2" />
    <path d="M21 28h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
)

/** 大模型配置面板：服务商与模型统一存后端数据库，选中模型供生成与讨论使用 */
export default function LlmSettings({
  formOpen,
  onFormOpenChange,
  onConfigsChanged,
}: LlmSettingsProps) {
  const { t } = useI18n()
  const [configs, setConfigs] = useState<LlmConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<LlmConfig>(createDraft)
  /** 编辑已有配置时数据库里是否已存有密钥（接口只回传脱敏值） */
  const [editingHasKey, setEditingHasKey] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const prevFormOpen = useRef(false)

  /** 拉取数据库里的配置列表；后端不可用时展示错误但不影响其他设置项 */
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true)
      setLoadError('')
      try {
        const items = await listLlmConfigs(signal)
        setConfigs(items)
      } catch {
        if (signal?.aborted) return
        setConfigs([])
        setLoadError(t('settings.llm.error.load'))
      } finally {
        if (!signal?.aborted) setLoading(false)
      }
    },
    [t],
  )

  // 首次挂载：清理旧版 localStorage 配置，并从后端读取最新列表
  useEffect(() => {
    try {
      localStorage.removeItem(LEGACY_STORAGE_KEY)
    } catch {
      // 隐私模式下忽略
    }
    const controller = new AbortController()
    // 首次拉取直接内联，避免 effect 同步调用含 setState 的 refresh
    listLlmConfigs(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setConfigs(items)
        setLoadError('')
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setConfigs([])
        setLoadError(t('settings.llm.error.load'))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [t])

  // 「新增配置」由面板标题栏触发；表单关闭时（含 Esc / 遮罩点击）重置编辑态
  useEffect(() => {
    const wasOpen = prevFormOpen.current
    prevFormOpen.current = formOpen
    if (formOpen && !wasOpen && editingId === null) {
      setDraft(createDraft())
      setEditingHasKey(false)
      setShowKey(false)
      setError('')
    } else if (!formOpen && wasOpen) {
      setEditingId(null)
      setDraft(createDraft())
      setEditingHasKey(false)
      setShowKey(false)
      setError('')
    }
  }, [formOpen, editingId])

  const openEdit = (config: LlmConfig) => {
    setEditingId(config.id)
    // 接口只回传脱敏密钥，编辑时输入框留空；保存时留空由后端保留原密钥
    setDraft({ ...config, apiKey: '', models: config.models.map((model) => ({ ...model })) })
    setEditingHasKey(Boolean(config.apiKey))
    setShowKey(false)
    setError('')
    onFormOpenChange(true)
  }

  const closeForm = () => {
    onFormOpenChange(false)
    setEditingId(null)
    setEditingHasKey(false)
    setShowKey(false)
    setError('')
  }

  const patchDraft = (patch: Partial<LlmConfig>) => setDraft((prev) => ({ ...prev, ...patch }))

  const patchModel = (id: string, patch: Partial<ModelItem>) =>
    setDraft((prev) => ({
      ...prev,
      models: prev.models.map((model) => (model.id === id ? { ...model, ...patch } : model)),
    }))

  const addModel = () => setDraft((prev) => ({ ...prev, models: [...prev.models, createModel()] }))

  const removeModel = (id: string) =>
    setDraft((prev) => ({ ...prev, models: prev.models.filter((model) => model.id !== id) }))

  const trimmedName = draft.name.trim()
  const trimmedApiKey = draft.apiKey.trim()
  const timeout = Number(draft.timeout)
  const timeoutValid = Number.isInteger(timeout) && timeout > 0 && timeout <= MAX_TIMEOUT
  const baseUrlValid = isValidHttpUrl(draft.baseUrl)
  const models = draft.models
    .map((model) => ({ ...model, name: model.name.trim() }))
    .filter((model) => model.name.length > 0)
  // 编辑时接口只回传脱敏密钥，留空表示保留原密钥；新建时密钥必填
  const keyReady = trimmedApiKey.length > 0 || Boolean(editingId && editingHasKey)
  const formValid =
    trimmedName.length > 0 && keyReady && timeoutValid && baseUrlValid && models.length > 0

  const save = async () => {
    if (saving) return
    if (!trimmedName) return setError(t('settings.llm.error.name'))
    if (!baseUrlValid) {
      return setError(
        draft.baseUrl.trim() ? t('settings.llm.error.baseUrlFormat') : t('settings.llm.error.baseUrl'),
      )
    }
    if (!timeoutValid) return setError(t('settings.llm.error.timeout'))
    if (!keyReady) return setError(t('settings.llm.error.apiKey'))
    if (models.length === 0) return setError(t('settings.llm.error.models'))

    const payload: LlmConfigInput = {
      name: trimmedName,
      baseUrl: draft.baseUrl.trim(),
      timeout: String(timeout),
      // 编辑时留空 → 后端保留原密钥；新建时这里必然非空
      apiKey: trimmedApiKey,
      models,
    }

    setSaving(true)
    setError('')
    try {
      if (editingId) await updateLlmConfig(editingId, payload)
      else await createLlmConfig(payload)
      await refresh()
      onConfigsChanged?.()
      closeForm()
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : t('settings.llm.error.save'))
    } finally {
      setSaving(false)
    }
  }

  const removeConfig = async (id: string) => {
    if (deletingId) return
    setDeletingId(id)
    setLoadError('')
    try {
      await deleteLlmConfig(id)
      await refresh()
      onConfigsChanged?.()
    } catch (err) {
      setLoadError(err instanceof Error && err.message ? err.message : t('settings.llm.error.delete'))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="llm-settings">
      <p className="llm-desc">{t('settings.llm.desc')}</p>

      {formOpen ? (
        <div className="llm-form-card" role="group" aria-label={t('settings.llm.form.new')}>
          <div className="llm-form-head">
            <h3>{t(editingId ? 'settings.llm.form.edit' : 'settings.llm.form.new')}</h3>
            <button className="llm-icon-btn" type="button" onClick={closeForm} aria-label={t('common.close')}>
              <CloseIcon />
            </button>
          </div>

          <div className="llm-form-body">
            <div className="llm-field-grid">
              <div className="llm-field">
                <label htmlFor="llm-name">
                  {t('settings.llm.name')}
                  <span className="llm-required" aria-hidden="true">*</span>
                </label>
                <input
                  id="llm-name"
                  value={draft.name}
                  placeholder={t('settings.llm.namePlaceholder')}
                  onChange={(event) => patchDraft({ name: event.target.value })}
                />
              </div>
              <div className="llm-field">
                <label htmlFor="llm-timeout">
                  {t('settings.llm.timeout')}
                  <span className="llm-required" aria-hidden="true">*</span>
                </label>
                <input
                  id="llm-timeout"
                  inputMode="numeric"
                  value={draft.timeout}
                  onChange={(event) => patchDraft({ timeout: event.target.value })}
                />
              </div>
            </div>

            <div className="llm-field">
              <label htmlFor="llm-base-url">
                {t('settings.llm.baseUrl')}
                <span className="llm-required" aria-hidden="true">*</span>
              </label>
              <input
                id="llm-base-url"
                value={draft.baseUrl}
                placeholder="https://api.openai.com/v1"
                aria-invalid={draft.baseUrl.trim().length > 0 && !baseUrlValid}
                onChange={(event) => patchDraft({ baseUrl: event.target.value })}
              />
              {draft.baseUrl.trim().length > 0 && !baseUrlValid && (
                <p className="llm-error">{t('settings.llm.error.baseUrlFormat')}</p>
              )}
            </div>

            <div className="llm-field">
              <label htmlFor="llm-api-key">
                {t('settings.llm.apiKey')}
                <span className="llm-required" aria-hidden="true">*</span>
              </label>
              <div className="llm-input-with-action">
                <input
                  id="llm-api-key"
                  type={showKey ? 'text' : 'password'}
                  autoComplete="off"
                  value={draft.apiKey}
                  placeholder={
                    editingId && editingHasKey
                      ? t('settings.llm.keyKeepHint')
                      : t('settings.llm.apiKeyPlaceholder')
                  }
                  onChange={(event) => patchDraft({ apiKey: event.target.value })}
                />
                <button
                  className="llm-icon-btn llm-key-toggle"
                  type="button"
                  onClick={() => setShowKey((prev) => !prev)}
                  aria-label={t(showKey ? 'settings.llm.hideKey' : 'settings.llm.showKey')}
                  title={t(showKey ? 'settings.llm.hideKey' : 'settings.llm.showKey')}
                >
                  <EyeIcon hidden={showKey} />
                </button>
              </div>
              {editingId && editingHasKey && (
                <p className="llm-hint">{t('settings.llm.keyKeepHint')}</p>
              )}
            </div>

            <div className="llm-models-head">
              <span>
                {t('settings.llm.models')}
                <span className="llm-required" aria-hidden="true">*</span>
              </span>
              <button className="llm-link-btn" type="button" onClick={addModel}>
                <PlusIcon />
                {t('settings.llm.addModel')}
              </button>
            </div>

            <div className="llm-model-list">
              {draft.models.map((model) => (
                <div className="llm-model-item" key={model.id}>
                  <div className="llm-model-row">
                    <input
                      className="llm-model-name"
                      value={model.name}
                      placeholder={t('settings.llm.modelPlaceholder')}
                      aria-label={t('settings.llm.models')}
                      onChange={(event) => patchModel(model.id, { name: event.target.value })}
                    />
                    <button
                      className="llm-icon-btn llm-delete-btn"
                      type="button"
                      onClick={() => removeModel(model.id)}
                      aria-label={t('settings.llm.deleteModel')}
                      title={t('settings.llm.deleteModel')}
                    >
                      <TrashIcon />
                    </button>
                  </div>
                </div>
              ))}
              {draft.models.length === 0 && (
                <button className="llm-models-empty" type="button" onClick={addModel}>
                  <PlusIcon />
                  {t('settings.llm.addModel')}
                </button>
              )}
            </div>

            {error && <p className="llm-error">{error}</p>}
          </div>

          <div className="llm-form-foot">
            <button className="llm-btn is-ghost" type="button" onClick={closeForm}>
              {t('common.cancel')}
            </button>
            <span
              className={`llm-save-wrap ${formValid && !saving ? '' : 'is-disabled'}`}
              data-tip={formValid ? undefined : t('settings.llm.saveHint')}
              title={formValid ? undefined : t('settings.llm.saveHint')}
            >
              <button
                className="llm-btn is-primary"
                type="button"
                disabled={!formValid || saving}
                onClick={() => void save()}
              >
                {saving ? t('settings.llm.saving') : t('settings.llm.save')}
              </button>
            </span>
          </div>
        </div>
      ) : loading ? (
        <div className="llm-empty">
          <span>{t('settings.llm.loading')}</span>
        </div>
      ) : loadError && configs.length === 0 ? (
        <div className="llm-empty">
          <span className="llm-error">{loadError}</span>
        </div>
      ) : configs.length === 0 ? (
        <div className="llm-empty">
          <EmptyIcon />
          <span>{t('settings.llm.empty')}</span>
        </div>
      ) : (
        <div className="llm-config-list">
          {loadError && <p className="llm-error">{loadError}</p>}
          {configs.map((config) => (
            <div className="llm-config-card" key={config.id}>
              <div className="llm-config-info">
                <div className="llm-config-name">{config.name}</div>
                <div className="llm-config-meta">
                  {config.baseUrl} · {t('settings.llm.modelCount', { n: config.models.length })}
                </div>
              </div>
              <div className="llm-config-actions">
                <button className="llm-link-btn" type="button" onClick={() => openEdit(config)}>
                  {t('settings.llm.edit')}
                </button>
                <button
                  className="llm-icon-btn"
                  type="button"
                  disabled={deletingId === config.id}
                  onClick={() => void removeConfig(config.id)}
                  aria-label={t('settings.llm.delete')}
                  title={t('settings.llm.delete')}
                >
                  <TrashIcon />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
