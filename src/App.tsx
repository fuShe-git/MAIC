import { useState } from 'react'
import { sendMessage } from './api/chat'
import './App.css'

const EXAMPLES = [
  '从零学 Python，30 分钟写出第一个程序',
  '用正弦函数讲讲傅里叶变换',
  '阿瓦隆桌游怎么玩',
]

const PLACEHOLDER = `输入你想学的任何内容，例如：
${EXAMPLES.map((item) => `[${item}]`).join('\n')}`

function ChevronDown() {
  return (
    <svg
      className="icon icon-xs"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M4 6.5 8 10l4-3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function App() {
  const [prompt, setPrompt] = useState('')
  const [deepMode, setDeepMode] = useState(true)
  const [reply, setReply] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit() {
    const text = prompt.trim()
    if (!text || loading) return

    setLoading(true)
    setError('')
    setReply('')

    try {
      setReply(await sendMessage(text))
    } catch (err) {
      setError(err instanceof Error ? err.message : '请求失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <header className="brand">
        <div className="brand-row">
          <img className="brand-icon" src="/img/bitbug_favicon.ico" alt="MAIC" />
          <h1 className="brand-name">MAIC</h1>
        </div>
      </header>

      <main className="composer">
        <div className="composer-head">
          <div className="user">
            <span className="avatar" aria-hidden="true" />
            <span className="user-name">同学</span>
            <ChevronDown />
          </div>

          <span className="composer-tip">准备好一起学习了吗?</span>

          <button className="model-switch" type="button">
            <svg className="icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" />
              <path
                d="M2 8h12M8 2c1.8 1.7 2.7 3.7 2.7 6S9.8 12.3 8 14c-1.8-1.7-2.7-3.7-2.7-6S6.2 3.7 8 2Z"
                stroke="currentColor"
                strokeWidth="1.2"
              />
            </svg>
            <span>API</span>
            <ChevronDown />
          </button>
        </div>

        <label className="sr-only" htmlFor="composer-input">
          输入你想学的任何内容
        </label>
        <textarea
          id="composer-input"
          className="composer-input"
          value={prompt}
          placeholder={PLACEHOLDER}
          onChange={(event) => setPrompt(event.target.value)}
        />

        <div className="composer-foot">
          <button className="icon-btn" type="button" aria-label="添加附件">
            <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M20 11.5 12.3 19a4.6 4.6 0 0 1-6.5-6.5l7.6-7.6a3 3 0 0 1 4.3 4.3l-7.6 7.6a1.5 1.5 0 0 1-2.1-2.1l6.9-6.9"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          <div className="composer-actions">
            <button
              className={`btn-deep${deepMode ? ' is-active' : ''}`}
              type="button"
              onClick={() => setDeepMode((value) => !value)}
            >
              <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 3v3M12 18v3M4.2 7.5l2.6 1.5M17.2 15l2.6 1.5M4.2 16.5l2.6-1.5M17.2 9l2.6-1.5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
                <circle cx="12" cy="12" r="3.2" stroke="currentColor" strokeWidth="1.6" />
              </svg>
              <span>深度交互</span>
            </button>

            <button
              className="btn-enter"
              type="button"
              disabled={!prompt.trim() || loading}
              onClick={handleSubmit}
            >
              <svg className="icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 19V5M6 11l6-6 6 6"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span>{loading ? '生成中…' : '进入课堂'}</span>
            </button>
          </div>
        </div>
      </main>

      {(loading || reply || error) && (
        <section className="answer" aria-live="polite">
          <div className="answer-head">
            <span className="answer-dot" aria-hidden="true" />
            <span className="answer-title">
              {loading ? '正在思考…' : error ? '出错了' : '模型回复'}
            </span>
          </div>
          {error ? (
            <p className="answer-text is-error">{error}</p>
          ) : (
            <p className="answer-text">{loading ? '正在生成回复，请稍候' : reply}</p>
          )}
        </section>
      )}
    </div>
  )
}

export default App
