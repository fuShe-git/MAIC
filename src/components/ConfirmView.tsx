import { useEffect, useState } from 'react'
import type { OutlineModule } from '../api/chat'
import GeneratingMark from './GeneratingMark'
import { useI18n } from '../i18n/context'
import './ConfirmView.css'

export type { OutlineModule }

export interface OutlineEditPayload {
  /** 当前编辑中的完整大纲（含用户新增页面） */
  catalogList: OutlineModule[]
}

export interface OutlinePanelProps {
  catalogList: OutlineModule[]
  status: 'loading' | 'ready' | 'regenerating' | 'error'
  errorMessage?: string
  /** 编辑中的大纲实时回传，便于直接切步骤时不丢失修改 */
  onDraftChange?: (catalogList: OutlineModule[]) => void
  /** 按当前编辑的大纲（含新增页面）重新生成大纲 */
  onRegenerateOutline: (payload: OutlineEditPayload) => void
}

function cloneModules(catalogList: OutlineModule[]): OutlineModule[] {
  return catalogList.map((module) => ({
    ind: module.ind,
    title: [...module.title],
    context: [...module.context],
    summary: module.summary ?? '',
    isNew: module.isNew,
  }))
}

function cleanModules(modules: OutlineModule[]): OutlineModule[] {
  return modules.map((module, index) => ({
    ind: index + 1,
    title: module.title.length ? [...module.title] : [''],
    context: [...module.context],
    summary: (module.summary ?? '').trim(),
    isNew: module.isNew,
  }))
}

/** 大纲确认面板：只展示每页主题与简介，支持拖动排序与按意见重新生成。 */
export default function ConfirmView({
  catalogList,
  status,
  errorMessage,
  onDraftChange,
  // onRegenerateOutline, // 重新生成大纲按钮已暂时注释，接口保留备用
}: OutlinePanelProps) {
  const { t } = useI18n()
  const [modules, setModules] = useState<OutlineModule[]>(() => cloneModules(catalogList))
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null)
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)

  // 父组件传入新大纲时重置编辑态：渲染期比较代替 effect 内同步 setState
  const [prevCatalogList, setPrevCatalogList] = useState(catalogList)
  if (catalogList !== prevCatalogList) {
    setPrevCatalogList(catalogList)
    setModules(cloneModules(catalogList))
  }

  useEffect(() => {
    onDraftChange?.(cleanModules(modules))
  }, [modules, onDraftChange])

  const busy = status === 'loading' || status === 'regenerating'
  // 重新生成大纲按钮暂时注释，以下可用性判断一并保留备用
  // const addedModules = modules.filter((module) => module.isNew)
  // const canRegenerate =
  //   !busy &&
  //   addedModules.length > 0 &&
  //   addedModules.every((module) => (module.title[0] ?? '').trim().length > 0)

  const updateModule = (index: number, patch: Partial<OutlineModule>) => {
    setModules((prev) => prev.map((module, i) => (i === index ? { ...module, ...patch } : module)))
  }

  const updateTitle = (moduleIndex: number, value: string) => {
    setModules((prev) =>
      prev.map((module, i) => {
        if (i !== moduleIndex) return module
        const title = [...module.title]
        title[0] = value
        return { ...module, title }
      }),
    )
  }

  const moveModule = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0) return
    setModules((prev) => {
      if (from >= prev.length || to >= prev.length) return prev
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
  }

  const finishDrag = () => {
    setDraggingIndex(null)
    setDragOverIndex(null)
  }

  const addModule = () => {
    // 新页面主题留空，由用户自行填写，避免默认文案被当成有效内容
    setModules((prev) => [
      ...prev,
      { ind: prev.length + 1, title: [''], context: [''], summary: '', isNew: true },
    ])
  }

  const removeModule = (index: number) => {
    setModules((prev) => {
      if (!prev[index]?.isNew) return prev
      return cleanModules(prev.filter((_, i) => i !== index))
    })
  }

  return (
    <section className="pw-fade-in flex min-h-0 flex-1 flex-col gap-3">
      <header className="shrink-0">
        <p className="m-0 text-[13px] leading-5 text-[var(--text-dim)]">{t('confirm.desc')}</p>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1 pw-scroll">
        {busy && modules.length === 0 ? (
          <GeneratingMark
            title={t('confirm.generatingTitle')}
            subtitle={t('confirm.generatingSubtitle')}
          />
        ) : (
          modules.map((module, moduleIndex) => (
            <article
              key={moduleIndex}
              onDragOver={(event) => {
                if (busy || draggingIndex === null) return
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
                if (dragOverIndex !== moduleIndex) setDragOverIndex(moduleIndex)
              }}
              onDragLeave={(event) => {
                if (
                  dragOverIndex === moduleIndex &&
                  !event.currentTarget.contains(event.relatedTarget as Node | null)
                ) {
                  setDragOverIndex(null)
                }
              }}
              onDrop={(event) => {
                event.preventDefault()
                if (draggingIndex !== null) moveModule(draggingIndex, moduleIndex)
                finishDrag()
              }}
              className={`pw-module rounded-2xl border bg-[var(--surface-2)] p-4 ${
                draggingIndex === moduleIndex ? 'is-dragging' : ''
              } ${dragOverIndex === moduleIndex && draggingIndex !== moduleIndex ? 'is-drag-over' : ''}`}
            >
              <div className="mb-3 flex items-center gap-2">
                <span
                  draggable={!busy}
                  onDragStart={(event) => {
                    setDraggingIndex(moduleIndex)
                    event.dataTransfer.effectAllowed = 'move'
                    event.dataTransfer.setData('text/plain', String(moduleIndex))
                  }}
                  onDragEnd={finishDrag}
                  className="pw-drag-handle"
                  title={t('confirm.dragHint')}
                  aria-label={t('confirm.dragHint')}
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true">
                    <circle cx="5" cy="3" r="1.25" />
                    <circle cx="11" cy="3" r="1.25" />
                    <circle cx="5" cy="8" r="1.25" />
                    <circle cx="11" cy="8" r="1.25" />
                    <circle cx="5" cy="13" r="1.25" />
                    <circle cx="11" cy="13" r="1.25" />
                  </svg>
                </span>
                <span className="pw-page-index">{t('confirm.pageIndex', { n: moduleIndex + 1 })}</span>
                {module.isNew ? (
                  <button
                    type="button"
                    title={t('confirm.deleteAdded')}
                    aria-label={t('confirm.deleteAdded')}
                    disabled={busy}
                    onClick={() => removeModule(moduleIndex)}
                    className="ml-auto flex h-6 w-6 cursor-pointer items-center justify-center rounded-lg border border-transparent text-[16px] leading-none text-[var(--text-dim)] transition hover:border-[#F0C4C0] hover:bg-[#FDECEC] hover:text-[#B3261E] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    ×
                  </button>
                ) : null}
              </div>

              <label className="mb-2 block text-[12px] font-medium text-[var(--text-dim)]">
                {t('confirm.pageTopic')}
                <input
                  className="pw-input mt-1 w-full rounded-xl border bg-[var(--bg)] px-3 py-2 text-[14px] text-[var(--text)] outline-none transition"
                  value={module.title[0] ?? ''}
                  placeholder={t('confirm.pageTopicPlaceholder')}
                  disabled={busy}
                  onChange={(event) => updateTitle(moduleIndex, event.target.value)}
                />
              </label>

              <label className="block text-[12px] font-medium text-[var(--text-dim)]">
                {t('confirm.pageSummary')}
                <textarea
                  className="pw-input mt-1 w-full resize-y rounded-xl border bg-[var(--bg)] px-3 py-2 text-[13px] leading-6 text-[var(--text)] outline-none transition"
                  value={module.summary ?? ''}
                  placeholder={t('confirm.pageSummaryPlaceholder')}
                  rows={2}
                  disabled={busy}
                  onChange={(event) => updateModule(moduleIndex, { summary: event.target.value })}
                />
              </label>
            </article>
          ))
        )}
      </div>

      {errorMessage ? (
        <p className="m-0 shrink-0 rounded-xl bg-[#FDECEC] px-3 py-2 text-[12px] text-[#B3261E]">
          {errorMessage}
        </p>
      ) : null}

      {!busy ? (
        <div className="shrink-0 rounded-2xl border border-[var(--border)] bg-[var(--surface-hover)] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <button
              type="button"
              className="cursor-pointer rounded-xl border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-2 text-[13px] font-medium text-[var(--text)] transition hover:border-[#7259E8] hover:text-[#5B45E0]"
              onClick={addModule}
            >
              {t('confirm.addCustom')}
            </button>
            {/* 重新生成大纲按钮暂时注释，保留代码备用
            <button
              type="button"
              className="cursor-pointer rounded-xl border border-[#DAD4FF] bg-[#F5F2FF] px-3 py-2 text-[13px] font-medium text-[#5B45E0] transition hover:bg-[#EBE5FF] disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => onRegenerateOutline({ catalogList: modules })}
              disabled={!canRegenerate}
            >
              重新生成大纲
            </button>
            */}
          </div>
        </div>
      ) : null}
    </section>
  )
}
