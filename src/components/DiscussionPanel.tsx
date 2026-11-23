import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { PageItem } from '../api/chat'
import { discussionStream } from '../api/discussion'
import type {
  DiscussionEvent,
  DiscussionMessage,
  DiscussionSource,
  DiscussionSourceItem,
} from '../api/discussion'
import { buildCoursePages, buildHistory, parsePageNumber } from '../utils/courseContext'
import './DiscussionPanel.css'

export interface DiscussionPanelProps {
  topic: string
  courseId: string
  pageList: PageItem[]
  pageLabel: string
  open: boolean
  onToggle: () => void
  /** 输入框聚焦时通知播放器暂停讲解 */
  onFocusInput?: () => void
}

const SOURCE_TEXT: Record<DiscussionSource, string> = {
  courseware: '课件内容',
  web: '网络资料',
  mixed: '课件 + 网络',
}

const newId = () =>
  `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`

export default function DiscussionPanel({
  topic,
  courseId,
  pageList,
  pageLabel,
  open,
  onToggle,
  onFocusInput,
}: DiscussionPanelProps) {
  const [messages, setMessages] = useState<DiscussionMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [source, setSource] = useState<DiscussionSource>('courseware')
  const [searching, setSearching] = useState(false)
  const [notice, setNotice] = useState('')

  const abortRef = useRef<AbortController | null>(null)
  const sendingRef = useRef(false)
  const firstQuestionRef = useRef(true)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    const list = listRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages])

  const patchMessage = useCallback((id: string, patch: Partial<DiscussionMessage>) => {
    setMessages((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)))
  }, [])

  const appendMessage = useCallback((id: string, text: string) => {
    setMessages((prev) =>
      prev.map((item) =>
        item.id === id ? { ...item, content: item.content + text } : item,
      ),
    )
  }, [])

  function stopGeneration() {
    const controller = abortRef.current
    if (!controller) return
    controller.abort()
    setSearching(false)
    setMessages((prev) =>
      prev.map((item, index) =>
        index === prev.length - 1 && item.role === 'assistant'
          ? {
              ...item,
              content: item.content ? `${item.content}\n\n（已停止）` : '（已停止）',
            }
          : item,
      ),
    )
  }

  function clearDiscussion() {
    if (sendingRef.current) abortRef.current?.abort()
    setMessages([])
    setInput('')
    setNotice('')
    setSearching(false)
    setSource('courseware')
    firstQuestionRef.current = true
  }

  async function handleSend() {
    const text = input.trim()
    if (!text || sendingRef.current) return

    sendingRef.current = true
    setSending(true)
    setInput('')
    setNotice('')

    const controller = new AbortController()
    abortRef.current = controller
    const assistantId = newId()
    const firstQuestion = firstQuestionRef.current
    firstQuestionRef.current = false

    const coursePages = firstQuestion ? buildCoursePages(pageList) : []
    const payload = {
      message: text,
      courseId,
      topic,
      pageNum: pageLabel,
      firstQuestion,
      coursePages,
      history: buildHistory(messages),
    }

    setMessages((prev) => [
      ...prev,
      { id: newId(), role: 'user', content: text },
      { id: assistantId, role: 'assistant', content: '', source: 'courseware', sources: [] },
    ])

    let gotDone = false
    let gotError = false
    let gotDelta = false

    const handleEvent = (event: DiscussionEvent) => {
      if (event.type === 'meta') {
        if (event.source) setSource(event.source)
        if (typeof event.searching === 'boolean') setSearching(event.searching)
        if (event.notice) setNotice(event.notice)
        return
      }
      if (event.type === 'delta') {
        gotDelta = true
        appendMessage(assistantId, event.text)
        return
      }
      if (event.type === 'sources') {
        patchMessage(assistantId, { sources: event.items })
        return
      }
      if (event.type === 'done') {
        gotDone = true
        setSearching(false)
        if (event.source) {
          setSource(event.source)
          patchMessage(assistantId, { source: event.source })
        }
        if (event.compressed) setNotice('课件正文较长，已压缩后参与回答，细节可能不完整')
        return
      }
      if (event.type === 'error') {
        gotError = true
        setSearching(false)
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  error: true,
                  content: item.content
                    ? `${item.content}\n\n讨论失败：${event.message}`
                    : `讨论失败：${event.message}`,
                }
              : item,
          ),
        )
      }
    }

    try {
      await discussionStream(payload, handleEvent, controller.signal)
    } catch (error: unknown) {
      if (!controller.signal.aborted) {
        gotError = true
        const detail = error instanceof Error ? error.message : '网络异常，请稍后重试'
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  error: true,
                  content: item.content
                    ? `${item.content}\n\n讨论失败：${detail}`
                    : `讨论失败：${detail}`,
                }
              : item,
          ),
        )
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      if (!controller.signal.aborted && !gotDone && !gotError) {
        const suffix = gotDelta ? '\n\n（连接中断，回答可能不完整）' : '连接已断开，请重试'
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? { ...item, error: true, content: item.content + suffix }
              : item,
          ),
        )
      }
      sendingRef.current = false
      setSending(false)
      setSearching(false)
      inputRef.current?.focus()
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    void handleSend()
  }

  const currentPage = parsePageNumber(pageLabel)
  const coursePagesCount = buildCoursePages(pageList).length

  return (
    <aside className={`dp-panel${open ? ' is-open' : ''}`} aria-hidden={!open}>
      <div className="dp-tabbar">
        <span className="dp-tab is-active">对话</span>
        <div className="dp-tab-actions">
          <button
            className="dp-tab-action"
            type="button"
            onClick={clearDiscussion}
            title="清空本次讨论"
            aria-label="清空本次讨论"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 5v14M5 12h14"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
          <button
            className="dp-tab-action"
            type="button"
            onClick={onToggle}
            title="收起对话"
            aria-label="收起对话"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <rect
                x="3"
                y="4"
                width="18"
                height="16"
                rx="2"
                stroke="currentColor"
                strokeWidth="1.6"
              />
              <path d="M15 4v16" stroke="currentColor" strokeWidth="1.6" />
            </svg>
          </button>
        </div>
      </div>

      <div className="dp-mode-bar">
        <span className="dp-mode-title">讨论模式</span>
        <span className="dp-mode-count">当前对话 {messages.length > 0 ? 1 : 0}</span>
      </div>

      <div className="dp-status">
        <span
          className={`dp-status-dot${searching || sending ? ' is-searching' : ''}`}
          aria-hidden="true"
        />
        <span>
          {searching
            ? '正在检索网络资料…'
            : sending
              ? '正在依据资料回答…'
              : coursePagesCount > 0
                ? `已附带整份课件内容 · 共 ${coursePagesCount} 页${currentPage ? ` · 当前第 ${currentPage} 页` : ''}`
                : '未获取到课件正文，将尝试联网检索'}
        </span>
      </div>

      {notice ? <p className="dp-notice">{notice}</p> : null}

      <div className="dp-messages" ref={listRef}>
        {messages.length === 0 ? (
          <div className="dp-empty">
            <p className="dp-section-title">当前对话</p>
            <div className="dp-thread-card">
              <span className="dp-thread-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none">
                  <path
                    d="M20 15a3 3 0 0 1-3 3H9l-5 3v-6a3 3 0 0 1-1-2.2V7a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3v8Z"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
              <span className="dp-thread-info">
                <span className="dp-thread-name">{topic || '当前课件'}</span>
                <span className="dp-thread-desc">实例讨论 · {coursePagesCount} 页课件</span>
              </span>
            </div>
            <p className="dp-empty-hint">
              输入问题开始讨论，首次提问会自动附带整份课件内容。
            </p>
          </div>
        ) : (
          messages.map((message, index) => {
            const isLast = index === messages.length - 1
            const shownSource = message.source ?? source
            const sourceItems: DiscussionSourceItem[] = message.sources ?? []
            return (
              <div
                className={`dp-message is-${message.role}${message.error ? ' is-error' : ''}`}
                key={message.id}
              >
                <div className="dp-bubble">
                  {message.content || (isLast && sending) ? (
                    <span className="dp-text">
                      {message.content}
                      {isLast && sending ? <span className="dp-caret" aria-hidden="true" /> : null}
                    </span>
                  ) : (
                    <span className="dp-thinking" aria-label="正在思考">
                      <i />
                      <i />
                      <i />
                    </span>
                  )}
                </div>
                {message.role === 'assistant' && message.content ? (
                  <div className="dp-message-meta">
                    <span className={`dp-source-tag is-${shownSource}`}>
                      {SOURCE_TEXT[shownSource]}
                    </span>
                    {sourceItems.map((item, itemIndex) =>
                      item.url ? (
                        <a
                          className="dp-source-tag is-link"
                          href={item.url}
                          key={`${item.label}-${itemIndex}`}
                          target="_blank"
                          rel="noreferrer"
                          title={item.snippet || item.label}
                        >
                          {item.label}
                        </a>
                      ) : (
                        <span
                          className="dp-source-tag"
                          key={`${item.label}-${itemIndex}`}
                          title={item.snippet || item.label}
                        >
                          {item.label}
                        </span>
                      ),
                    )}
                  </div>
                ) : null}
              </div>
            )
          })
        )}
      </div>

      <div className="dp-compose">
        <textarea
          ref={inputRef}
          className="dp-input"
          value={input}
          rows={2}
          placeholder="围绕这节课提问，或追问刚才的回答…"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={onFocusInput}
          disabled={sending}
        />
        <div className="dp-compose-foot">
          <span className="dp-tip">Enter 发送 · Shift+Enter 换行</span>
          {sending ? (
            <button className="dp-send is-stop" type="button" onClick={stopGeneration}>
              停止
            </button>
          ) : (
            <button
              className="dp-send"
              type="button"
              onClick={() => void handleSend()}
              disabled={!input.trim()}
            >
              发送
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}
