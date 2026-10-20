import { color, severity, type, type SeverityKey } from '../theme'

// Severity reads by SHAPE + color + label, never hue alone (colorblind-safe, and
// it looks like a DRC marker, not a UI icon). Square = must-fix, triangle =
// verify, dot = cosmetic.
export function SeverityMark({ sev, size = 12 }: { sev: SeverityKey; size?: number }) {
  const tone = severity[sev]?.tone ?? color.inkSecondary
  const mark = severity[sev]?.mark ?? 'dot'
  const s = size
  return (
    <svg width={s} height={s} viewBox="0 0 12 12" aria-hidden style={{ display: 'block' }}>
      {mark === 'square' && <rect x="1.5" y="1.5" width="9" height="9" rx="1" fill={tone} />}
      {mark === 'triangle' && <path d="M6 1.5 L11 10.5 L1 10.5 Z" fill={tone} />}
      {mark === 'dot' && <circle cx="6" cy="6" r="3.4" fill={tone} />}
    </svg>
  )
}

// The Ohmen mark without its app-icon tile (docs/assets/logo.svg): copper traces
// and pads on the bench, the pad under review inside an agent-purple Ω.
export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={(size * 600) / 840}
      viewBox="112 268 840 600"
      aria-hidden
      style={{ display: 'block', flexShrink: 0 }}
    >
      <g fill="none" stroke={color.copper} strokeWidth="56" strokeLinecap="round" strokeLinejoin="round">
        <path d="M196 352 H420 L588 520 H760" />
        <path d="M196 680 H372 L476 784 H760" />
      </g>
      <g fill={color.copper}>
        <circle cx="196" cy="352" r="64" />
        <circle cx="196" cy="680" r="64" />
        <circle cx="760" cy="784" r="64" />
        <circle cx="776" cy="520" r="72" />
      </g>
      <g fill={color.benchVoid}>
        <circle cx="196" cy="352" r="24" />
        <circle cx="196" cy="680" r="24" />
        <circle cx="760" cy="784" r="24" />
        <circle cx="776" cy="520" r="28" />
      </g>
      <path
        d="M628 646 H712 A141 141 0 1 1 840 646 H924"
        fill="none"
        stroke={color.agent}
        strokeWidth="28"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

// Big mono numeral + a quiet sentence-case label beneath (the mixer "340").
export function StatNumeral({
  value,
  label,
  tone = color.inkPrimary,
}: {
  value: number | string
  label: string
  tone?: string
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
      <span
        style={{
          fontFamily: type.mono,
          fontSize: type.size.xxl,
          lineHeight: 1,
          fontWeight: type.weight.med,
          color: tone,
          letterSpacing: '-0.01em',
        }}
      >
        {value}
      </span>
      <span style={{ fontSize: type.size.xs, color: color.inkSecondary, marginTop: 4 }}>{label}</span>
    </div>
  )
}
