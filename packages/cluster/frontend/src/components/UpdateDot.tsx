interface UpdateDotProps {
  className?: string
}

export default function UpdateDot({ className = '' }: UpdateDotProps) {
  return (
    <span
      role="status"
      aria-label="읽지 않은 업데이트 있음"
      title="읽지 않은 업데이트 있음"
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[#0075de] ${className}`}
    />
  )
}
