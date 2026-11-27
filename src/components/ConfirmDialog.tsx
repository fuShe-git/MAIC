import { useEffect, useRef } from 'react'
import { useI18n } from '../i18n/context'
import './ConfirmDialog.css'

interface ConfirmDialogProps {
  open: boolean
  /** 标题（例如「删除课堂?」） */
  title: string
  /** 补充说明，可省略 */
  description?: string
  /** 请求失败等错误提示 */
  error?: string
  confirmLabel?: string
  cancelLabel?: string
  /** 危险操作：确认按钮用红色 */
  danger?: boolean
  /** 提交中：禁用按钮并禁止 Esc / 点遮罩关闭 */
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 通用二次确认弹窗：遮罩 + 标题 + 取消/确认。
 * 说明：类名统一用 confirm-modal 前缀，避免和 ConfirmView 的 .confirm-* 互相污染。
 */
export default function ConfirmDialog({
  open,
  title,
  description,
  error,
  confirmLabel,
  cancelLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useI18n()
  // 用 ref 持有回调：避免父组件重渲染导致 effect 重跑、监听器反复解绑
  const cancelRef = useRef(onCancel)
  const busyRef = useRef(busy)

  useEffect(() => {
    cancelRef.current = onCancel
    busyRef.current = busy
  }, [onCancel, busy])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyRef.current) cancelRef.current()
    }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = prevOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!open) return null

  return (
    <div
      className="confirm-modal-mask"
      role="presentation"
      onMouseDown={(event) => {
        // 只有点遮罩本身才关闭；点弹窗内部不关闭
        if (event.target === event.currentTarget && !busy) onCancel()
      }}
    >
      <div
        className="confirm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="confirm-modal-title">{title}</h2>
        {description ? <p className="confirm-modal-desc">{description}</p> : null}
        {error ? (
          <p className="confirm-modal-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="confirm-modal-actions">
          <button
            className="confirm-modal-btn is-ghost"
            type="button"
            disabled={busy}
            onClick={onCancel}
          >
            {cancelLabel ?? t('common.cancel')}
          </button>
          <button
            className={`confirm-modal-btn ${danger ? 'is-danger' : 'is-primary'}`}
            type="button"
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel ?? t('common.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
