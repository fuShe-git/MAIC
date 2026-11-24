import { useEffect, useState } from 'react'
import { useI18n } from '../i18n/context'
import {
  getEmbeddingConfig,
  saveEmbeddingConfig,
  testEmbeddingConfig,
} from '../api/embeddingConfig'
import type { EmbeddingConfig, EmbeddingConfigInput } from '../api/embeddingConfig'

interface Draft {
  baseUrl: string
  model: string
  dimensions: string
  timeout: string
  apiKey: string
}

type FieldKey = keyof Draft

const DEFAULT_DRAFT: Draft = {
  baseUrl: '',
  model: '',
  dimensions: '1024',
  timeout: '120',
  apiKey: '',
}

const DIMENSION_MAX = 4096
const TIMEOUT_MAX = 3600

function draftFrom(config: EmbeddingConfig): Draft {
  // 平台默认值只作为提示展示，不写进输入框；用户填写并保存后才算自定义配置。
  // 接口只回传脱敏密钥，编辑框留空；留空保存表示保留原密钥。
  return {
    baseUrl: config.isDefault ? '' : config.baseUrl,
    model: config.isDefault ? '' : config.model,
    dimensions: config.dimensions,
    timeout: config.timeout,
    apiKey: '',
  }
}

function isValidHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

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

/** Embedding 设置：兼容 OpenAI 协议的向量化服务，全局只有一份配置 */
export default function EmbeddingSettings() {
  const { t } = useI18n()
  const [config, setConfig] = useState<EmbeddingConfig | null>(null)
  const [draft, setDraft] = useState<Draft>(DEFAULT_DRAFT)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let alive = true
    setLoading(true)
    getEmbeddingConfig(controller.signal)
      .then((data) => {
        if (!alive) return
        setConfig(data)
        setDraft(draftFrom(data))
      })
      .catch(() => {
        if (alive) setNotice({ text: t('settings.embedding.error.load'), error: true })
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
      controller.abort()
    }
  }, [t])

  const patchDraft = (patch: Partial<Draft>) => setDraft((prev) => ({ ...prev, ...patch }))

  const baseUrlPlaceholder =
    config?.isDefault && config.baseUrl ? config.baseUrl : 'https://api.openai.com/v1'
  const modelPlaceholder = config?.isDefault && config.model ? config.model : 'text-embedding-v4'

  const baseUrl = draft.baseUrl.trim()
  const model = draft.model.trim()
  const dimensions = Number(draft.dimensions)
  const timeout = Number(draft.timeout)
  const errors: Partial<Record<FieldKey, string>> = {}
  if (!baseUrl) errors.baseUrl = t('settings.embedding.error.baseUrl')
  else if (!isValidHttpUrl(baseUrl)) errors.baseUrl = t('settings.embedding.error.baseUrlFormat')
  if (!model) errors.model = t('settings.embedding.error.model')
  if (!Number.isInteger(dimensions) || dimensions < 1 || dimensions > DIMENSION_MAX) {
    errors.dimensions = t('settings.embedding.error.dimensions')
  }
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > TIMEOUT_MAX) {
    errors.timeout = t('settings.embedding.error.timeout')
  }
  const hasError = Object.keys(errors).length > 0
  const payload: EmbeddingConfigInput = {
    baseUrl,
    model,
    dimensions,
    timeout,
    apiKey: draft.apiKey.trim(),
  }

  const test = async () => {
    if (testing || saving) return
    if (hasError) {
      setNotice({ text: Object.values(errors)[0] ?? '', error: true })
      return
    }
    setTesting(true)
    setNotice(null)
    try {
      const result = await testEmbeddingConfig(payload)
      setNotice({
        text: result.message || t('settings.embedding.testSuccess'),
        error: !result.ok,
      })
    } catch (err) {
      setNotice({ text: errorMessage(err, t('settings.embedding.error.test')), error: true })
    } finally {
      setTesting(false)
    }
  }

  const save = async () => {
    if (saving || testing) return
    if (hasError) {
      setNotice({ text: Object.values(errors)[0] ?? '', error: true })
      return
    }
    setSaving(true)
    setNotice(null)
    try {
      const saved = await saveEmbeddingConfig(payload)
      setConfig(saved)
      setDraft(draftFrom(saved))
      setShowKey(false)
      setNotice({ text: t('settings.embedding.saved'), error: false })
    } catch (err) {
      setNotice({ text: errorMessage(err, t('settings.embedding.error.save')), error: true })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="llm-settings embedding-settings">
      <p className="llm-desc">{t('settings.embedding.desc')}</p>
      <div className="embedding-notice">
        {config && !config.isDefault
          ? t('settings.embedding.customHint')
          : t('settings.embedding.defaultHint')}
      </div>

      {loading ? (
        <div className="llm-empty">
          <span>{t('settings.embedding.loading')}</span>
        </div>
      ) : (
        <div className="llm-form-card">
          <div className="llm-form-body">
            <div className="llm-field">
              <label htmlFor="embedding-base-url">
                {t('settings.embedding.baseUrl')}
                <span className="llm-required" aria-hidden="true">*</span>
              </label>
              <input
                id="embedding-base-url"
                value={draft.baseUrl}
                placeholder={baseUrlPlaceholder}
                aria-invalid={Boolean(errors.baseUrl)}
                onChange={(event) => patchDraft({ baseUrl: event.target.value })}
              />
              {errors.baseUrl && <p className="llm-error">{errors.baseUrl}</p>}
            </div>

            <div className="llm-field-grid">
              <div className="llm-field">
                <label htmlFor="embedding-model">
                  {t('settings.embedding.model')}
                  <span className="llm-required" aria-hidden="true">*</span>
                </label>
                <input
                  id="embedding-model"
                  value={draft.model}
                  placeholder={modelPlaceholder}
                  aria-invalid={Boolean(errors.model)}
                  onChange={(event) => patchDraft({ model: event.target.value })}
                />
                {errors.model && <p className="llm-error">{errors.model}</p>}
              </div>

              <div className="llm-field">
                <label htmlFor="embedding-dimensions">
                  {t('settings.embedding.dimensions')}
                  <span className="llm-required" aria-hidden="true">*</span>
                </label>
                <input
                  id="embedding-dimensions"
                  inputMode="numeric"
                  value={draft.dimensions}
                  placeholder="1024"
                  aria-invalid={Boolean(errors.dimensions)}
                  onChange={(event) => patchDraft({ dimensions: event.target.value })}
                />
                {errors.dimensions && <p className="llm-error">{errors.dimensions}</p>}
              </div>

              <div className="llm-field">
                <label htmlFor="embedding-timeout">
                  {t('settings.embedding.timeout')}
                  <span className="llm-required" aria-hidden="true">*</span>
                </label>
                <input
                  id="embedding-timeout"
                  inputMode="numeric"
                  value={draft.timeout}
                  placeholder="120"
                  aria-invalid={Boolean(errors.timeout)}
                  onChange={(event) => patchDraft({ timeout: event.target.value })}
                />
                {errors.timeout && <p className="llm-error">{errors.timeout}</p>}
              </div>

              <div className="llm-field">
                <label htmlFor="embedding-api-key">{t('settings.embedding.apiKey')}</label>
                <div className="llm-input-with-action">
                  <input
                    id="embedding-api-key"
                    type={showKey ? 'text' : 'password'}
                    autoComplete="off"
                    value={draft.apiKey}
                    placeholder={t('settings.embedding.apiKeyPlaceholder')}
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
              </div>
            </div>

            {notice && (
              <p className={notice.error ? 'llm-error' : 'embedding-ok'}>{notice.text}</p>
            )}
          </div>

          <div className="llm-form-foot">
            <button
              className="llm-btn is-ghost"
              type="button"
              disabled={testing || saving}
              onClick={() => void test()}
            >
              {testing ? t('settings.embedding.testing') : t('settings.embedding.test')}
            </button>
            <button
              className="llm-btn is-primary"
              type="button"
              disabled={saving || testing}
              onClick={() => void save()}
            >
              {saving ? t('settings.embedding.saving') : t('settings.embedding.save')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
