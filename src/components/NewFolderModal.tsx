import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n/context'
import './NewFolderModal.css'

interface NewFolderModalProps {
  open: boolean
  /** 取消 / 关闭 */
  onClose: () => void
  /** 确认创建，参数为已 trim 的名称 */
  onCreate: (name: string) => void
}

/** 新建文件夹弹窗：只有名称一个字段 */
export default function NewFolderModal({ open, onClose, onCreate }: NewFolderModalProps) {
  const { t } = useI18n()
  const [name, setName] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)
  // 用 ref 持有 onClose：避免父组件重渲染时 effect 重跑、把已输入的名称清掉
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  // 每次打开重置输入并聚焦，同时支持 Esc 关闭
  useEffect(() => {
    if (!open) return
    setName('')
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current()
    }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(timer)
      document.body.style.overflow = prevOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!open) return null

  const trimmed = name.trim()
  const submit = () => {
    if (!trimmed) return
    onCreate(trimmed)
  }

  return (
    <div
      className="folder-mask"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="folder-modal" role="dialog" aria-modal="true" aria-label={t('folder.new')}>
        <header className="folder-modal-head">
          <h2 className="folder-modal-title">{t('folder.new')}</h2>
          <button
            className="folder-modal-close"
            type="button"
            onClick={onClose}
            aria-label={t('common.close')}
            title={t('common.close')}
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <p className="folder-modal-desc">{t('folder.desc')}</p>

        <label className="folder-modal-label" htmlFor="folder-name-input">
          {t('folder.nameLabel')}
        </label>
        <input
          id="folder-name-input"
          ref={inputRef}
          className="folder-modal-input"
          value={name}
          placeholder={t('folder.namePlaceholder')}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submit()
          }}
        />
        <p className="folder-modal-hint">{t('folder.nameHint')}</p>

        <footer className="folder-modal-foot">
          <button className="folder-btn is-ghost" type="button" onClick={onClose}>
            {t('folder.cancel')}
          </button>
          <button className="folder-btn is-primary" type="button" disabled={!trimmed} onClick={submit}>
            {t('folder.create')}
          </button>
        </footer>
      </div>
    </div>
  )
}
