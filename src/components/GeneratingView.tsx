import { useEffect, useState } from 'react'
import type { ChatResponse, ProgressStep } from '../api/chat'
import './GeneratingView.css'

/** 等待页所处阶段：进行中 / 重试中 / 最终失败 / 闲聊完成 / 课件就绪 */
export type WaitStatus = 'running' | 'retrying' | 'failed' | 'reply' | 'ready'

export interface GeneratingViewProps {
  /** 当前阶段 */
  status: WaitStatus
  /** 当前推进到的步骤；null 表示还没收到任何进度 */
  step: ProgressStep | null
  /** 已完成页数 / 总页数（页面生成步骤） */
  pagesCurrent: number
  pagesTotal: number
  /** 已完成数 / 总数（音频合成步骤） */
  audioCurrent: number
  audioTotal: number
  /** 闲聊回复文本 或 错误信息 */
  message: string
  /** 讲解主题（step=topic 时展示「讲解主题：xxx」） */
  topic: string
  /** 课件数据（status=ready 时有值） */
  lecture: ChatResponse | null
  /** 返回首页 */
  onBack: () => void
  /** 重新生成（失败后） */
  onRetry: () => void
  /** 开始学习（课件就绪后） */
  onStart: () => void
}

/** 步骤清单定义，顺序即展示顺序 */
const STEPS: { key: ProgressStep; name: string }[] = [
  { key: 'check', name: '开始检查' },
  { key: 'topic', name: '确认讲解主题' },
  { key: 'outline', name: '生成大纲和页面' },
  { key: 'pages', name: '页面生成中' },
  { key: 'audio', name: '音频合成中' },
  { key: 'assemble', name: '课件组装中' },
]

const stepIndex = (step: ProgressStep | null): number =>
  step ? STEPS.findIndex((item) => item.key === step) : -1

/** 卡片大标题 / 副标题，随状态与步骤切换 */
function useHeader(props: GeneratingViewProps): { title: string; subtitle: string } {
  const { status, step, topic, pagesCurrent, pagesTotal, audioCurrent, audioTotal } = props
  if (status === 'ready') return { title: '课程已生成', subtitle: '准备好了，开始学习吧' }
  if (status === 'failed') return { title: '生成出错', subtitle: '可点击下方按钮重新生成' }
  if (status === 'reply') return { title: '分析完成', subtitle: '看看 MAIC 怎么说' }
  if (status === 'retrying') return { title: '正在重试', subtitle: '已自动重新开始生成' }

  switch (step) {
    case 'topic':
      return { title: '确认讲解主题', subtitle: topic ? `讲解主题：${topic}` : '正在确定讲解主题…' }
    case 'outline':
      return { title: '生成课程大纲', subtitle: '正在构建学习路径…' }
    case 'pages':
      return {
        title: '正在生成页面',
        subtitle: pagesTotal > 0 ? `第 ${Math.min(pagesCurrent + 1, pagesTotal)} / ${pagesTotal} 页` : '请稍候…',
      }
    case 'audio':
      return {
        title: '正在合成音频',
        subtitle: audioTotal > 0 ? `第 ${Math.min(audioCurrent + 1, audioTotal)} / ${audioTotal} 页` : '请稍候…',
      }
    case 'assemble':
      return { title: '正在组装课件', subtitle: '马上就好…' }
    default:
      return { title: '开始检查', subtitle: '正在判断是否可以讲解…' }
  }
}

function CheckIcon() {
  return (
    <svg className="gv-check" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3.5 8.5 6.5 11.5 12.5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function GeneratingView(props: GeneratingViewProps) {
  const { status, step, pagesCurrent, pagesTotal, audioCurrent, audioTotal, message, topic } = props
  const header = useHeader(props)
  const current = stepIndex(step)
  const working = status === 'running' || status === 'retrying'

  // 已等待时长（秒），仅进行中/重试中计时
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [working])
  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`

  // 每个步骤右侧的计数/摘要文本；拿不到总数则留空，只显示步骤名
  const countOf = (key: ProgressStep): string => {
    if (key === 'topic' && topic) return `讲解主题：${topic}`
    if (key === 'pages' && pagesTotal > 0) return `${pagesCurrent} / ${pagesTotal} 页`
    if (key === 'audio' && audioTotal > 0) return `${audioCurrent} / ${audioTotal} 页`
    return ''
  }

  return (
    <div className="gv-page">
      <button className="gv-back" type="button" onClick={props.onBack}>
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M10 3.5 5.5 8 10 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        返回首页
      </button>

      <div className="gv-orb gv-orb-blue" aria-hidden="true" />
      <div className="gv-orb gv-orb-purple" aria-hidden="true" />

      <main className="gv-card" key={status === 'ready' ? 'ready' : 'card'}>
        <div className="gv-dots" aria-hidden="true">
          <span className={`gv-dot${working ? ' is-active' : ''}`} />
          <span className={`gv-dot${status === 'reply' || status === 'ready' ? ' is-done' : working ? 'is-pulse' : ''}`} />
          <span className={`gv-dot${status === 'ready' ? ' is-done' : ''}`} />
        </div>

        <div className={`gv-badge${status === 'ready' ? ' is-ready' : status === 'failed' ? ' is-failed' : ''}`} aria-hidden="true">
          {status === 'ready' ? <CheckIcon /> : <span className="gv-badge-core" />}
        </div>

        <h1 className="gv-title">{header.title}</h1>
        <p className="gv-subtitle">{header.subtitle}</p>

        {/* 步骤清单：进行中/重试中/失败时展示 */}
        {(status === 'running' || status === 'retrying' || status === 'failed') && (
          <ol className="gv-steps">
            {STEPS.map((item, index) => {
              const state = index < current ? 'done' : index === current ? 'active' : 'pending'
              return (
                <li className={`gv-step is-${state}`} key={item.key}>
                  <span className="gv-step-mark">{state === 'done' ? <CheckIcon /> : <i />}</span>
                  <span className="gv-step-name">{item.name}</span>
                  <span className="gv-step-count">{countOf(item.key)}</span>
                </li>
              )
            })}
          </ol>
        )}

        {/* 闲聊完成：卡片内直接展示回复文本 */}
        {status === 'reply' && <p className="gv-reply">{message}</p>}

        {/* 重试中：错误原因 + 重试提示（进度已由后端重新开始） */}
        {status === 'retrying' && <p className="gv-banner is-warn">{message}</p>}

        {/* 最终失败：错误信息 + 重新生成，不展示开始学习 */}
        {status === 'failed' && (
          <>
            <p className="gv-banner is-error">{message}</p>
            <button className="gv-btn is-ghost" type="button" onClick={props.onRetry}>
              重新生成
            </button>
          </>
        )}

        {/* 课件就绪：出现开始学习按钮 */}
        {status === 'ready' && (
          <button className="gv-btn is-primary" type="button" onClick={props.onStart}>
            开始学习
            <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M6 3.5 10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}

        {props.lecture && status === 'ready' && (
          <p className="gv-meta">共 {props.lecture.pageList.length} 个模块 · {props.lecture.topic}</p>
        )}
      </main>

      {working && (
        <footer className="gv-foot">
          <span className="gv-spinner" aria-hidden="true" />
          <span>AI智能体工作中…</span>
          <span className="gv-clock">{clock}</span>
        </footer>
      )}
    </div>
  )
}
