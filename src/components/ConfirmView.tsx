import { useState } from 'react'
import type { OutlineModule } from '../api/chat'
import './ConfirmView.css'

export type { OutlineModule }

interface ConfirmViewProps {
  /** 待确认的大纲（可编辑） */
  catalogList: OutlineModule[]
  /** 确认并生成：回传编辑后的大纲 */
  onConfirm: (catalogList: OutlineModule[]) => void
  /** 取消生成 */
  onCancel: () => void
  /** 是否正在提交（禁用按钮） */
  busy?: boolean
}

/**
 * 人工确认大纲视图（v4）：
 * 大纲产出后暂停生成，用户可增删改每个模块的标题与讲稿，确认后只重走 page/audio。
 */
export default function ConfirmView({ catalogList, onConfirm, onCancel, busy }: ConfirmViewProps) {
  const [modules, setModules] = useState<OutlineModule[]>(() =>
    catalogList.map((module) => ({
      ind: module.ind,
      title: [...module.title],
      context: [...module.context],
    })),
  )

  const updateModule = (index: number, patch: Partial<OutlineModule>) => {
    setModules((prev) => prev.map((module, i) => (i === index ? { ...module, ...patch } : module)))
  }

  const updatePoint = (moduleIndex: number, pointIndex: number, key: 'title' | 'context', value: string) => {
    setModules((prev) =>
      prev.map((module, i) => {
        if (i !== moduleIndex) return module
        const next = [...module[key]]
        next[pointIndex] = value
        return { ...module, [key]: next }
      }),
    )
  }

  const addPoint = (moduleIndex: number) => {
    setModules((prev) =>
      prev.map((module, i) =>
        i === moduleIndex
          ? { ...module, title: [...module.title, '新知识点'], context: [...module.context, ''] }
          : module,
      ),
    )
  }

  const removePoint = (moduleIndex: number, pointIndex: number) => {
    setModules((prev) =>
      prev.map((module, i) => {
        if (i !== moduleIndex || module.title.length <= 1) return module
        return {
          ...module,
          title: module.title.filter((_, p) => p !== pointIndex),
          context: module.context.filter((_, p) => p !== pointIndex),
        }
      }),
    )
  }

  const removeModule = (moduleIndex: number) => {
    setModules((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== moduleIndex)))
  }

  const handleConfirm = () => {
    // 重新连续编号，保证 ind 与页面/音频一一对应
    const cleaned = modules
      .map((module, i) => ({
        ind: i + 1,
        title: module.title.filter((t) => t.trim()),
        context: module.context.slice(0, module.title.length),
      }))
      .filter((module) => module.title.length > 0)
    onConfirm(cleaned)
  }

  return (
    <div className="confirm-root">
      <div className="confirm-card">
        <div className="confirm-header">
          <h1>请确认讲解大纲</h1>
          <p>可以修改标题与讲稿、增删知识点，确认后开始生成页面与音频</p>
        </div>

        <div className="confirm-body">
          {modules.map((module, moduleIndex) => (
            <div className="confirm-module" key={moduleIndex}>
              <div className="confirm-module-head">
                <span className="confirm-module-ind">第 {moduleIndex + 1} 页</span>
                <button
                  type="button"
                  className="confirm-link danger"
                  onClick={() => removeModule(moduleIndex)}
                  disabled={busy || modules.length <= 1}
                >
                  删除本页
                </button>
              </div>

              {module.title.map((title, pointIndex) => (
                <div className="confirm-point" key={pointIndex}>
                  <input
                    className="confirm-title-input"
                    value={title}
                    placeholder="知识点标题"
                    disabled={busy}
                    onChange={(e) => updatePoint(moduleIndex, pointIndex, 'title', e.target.value)}
                  />
                  <textarea
                    className="confirm-context-input"
                    value={module.context[pointIndex] ?? ''}
                    placeholder="口播讲稿（可直接朗读的连贯讲述）"
                    rows={3}
                    disabled={busy}
                    onChange={(e) => updatePoint(moduleIndex, pointIndex, 'context', e.target.value)}
                  />
                  <button
                    type="button"
                    className="confirm-link danger"
                    onClick={() => removePoint(moduleIndex, pointIndex)}
                    disabled={busy || module.title.length <= 1}
                  >
                    删除知识点
                  </button>
                </div>
              ))}

              <button
                type="button"
                className="confirm-link"
                onClick={() => addPoint(moduleIndex)}
                disabled={busy}
              >
                + 添加知识点
              </button>
            </div>
          ))}
        </div>

        <div className="confirm-actions">
          <button type="button" className="ghost-btn" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button type="button" className="primary-btn" onClick={handleConfirm} disabled={busy}>
            {busy ? '正在生成…' : '确认并生成'}
          </button>
        </div>
      </div>
    </div>
  )
}
