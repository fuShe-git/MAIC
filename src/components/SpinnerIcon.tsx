/** 加载中：不完整的旋转圆环，尺寸由外层 svg 规则控制 */
export default function SpinnerIcon() {
  return (
    <svg className="spin-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.4" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <path
        d="M20.4 12A8.4 8.4 0 0 0 12 3.6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  )
}
