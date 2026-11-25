import './GeneratingMark.css'

interface GeneratingMarkProps {
  title: string
  subtitle?: string
}

/** 生成中的统一标记：圆形图标 + 标题 + 副标题 + 简约骨架条。 */
export default function GeneratingMark({ title, subtitle }: GeneratingMarkProps) {
  return (
    <div className="pw-gen-mark" role="status" aria-live="polite">
      <div className="pw-gen-mark-core" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path
            d="M12 3.25l1.35 3.9 3.9 1.35-3.9 1.35L12 13.75l-1.35-3.9-3.9-1.35 3.9-1.35L12 3.25Z"
            fill="currentColor"
          />
          <path
            d="M18.4 13.6l.72 2.08 2.08.72-2.08.72-.72 2.08-.72-2.08-2.08-.72 2.08-.72.72-2.08Z"
            fill="currentColor"
            opacity="0.75"
          />
          <path
            d="M5.5 14.6l.52 1.5 1.5.52-1.5.52-.52 1.5-.52-1.5-1.5-.52 1.5-.52.52-1.5Z"
            fill="currentColor"
            opacity="0.6"
          />
        </svg>
      </div>
      <strong className="pw-gen-mark-title">{title}</strong>
      {subtitle ? <span className="pw-gen-mark-subtitle">{subtitle}</span> : null}
      <div className="pw-gen-mark-bars" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>
    </div>
  )
}
