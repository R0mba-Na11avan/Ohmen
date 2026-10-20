import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { color, radius } from '../theme'

type Side = 'up' | 'down'

// A floating panel pinned to its trigger. It lives in a portal on <body> with
// fixed positioning, so no scrolling pane can clip it, and it opens up or down
// ('auto' picks the side with room). Outside clicks and Esc close it; the
// caller keeps it mounted (`closing`) while the exit animation plays.
export function AnchoredPopover({
  anchorRef,
  open,
  closing,
  onClose,
  onExited,
  prefer = 'down',
  width,
  maxHeight = 460,
  children,
}: {
  anchorRef: RefObject<HTMLElement | null>
  open: boolean
  closing: boolean
  onClose: () => void
  onExited: () => void
  prefer?: Side | 'auto'
  width: number
  maxHeight?: number
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ side: Side; style: CSSProperties } | null>(null)
  const shown = open || closing

  useLayoutEffect(() => {
    if (!shown) {
      setPos(null)
      return
    }
    const place = () => {
      const a = anchorRef.current
      if (!a) return
      const r = a.getBoundingClientRect()
      const below = window.innerHeight - r.bottom
      const above = r.top
      const side: Side =
        prefer === 'auto' ? (below >= Math.min(maxHeight, 240) || below > above ? 'down' : 'up') : prefer
      const room = (side === 'down' ? below : above) - 12
      const w = Math.min(width, window.innerWidth - 16)
      const right = Math.min(window.innerWidth - r.right, window.innerWidth - w - 8)
      setPos({
        side,
        style: {
          right: Math.max(8, right),
          width: w,
          maxHeight: Math.max(160, Math.min(maxHeight, room)),
          ...(side === 'down' ? { top: r.bottom + 6 } : { bottom: window.innerHeight - r.top + 6 }),
        },
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [shown, prefer, width, maxHeight, anchorRef])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!ref.current?.contains(t) && !anchorRef.current?.contains(t)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      onClose()
      anchorRef.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose, anchorRef])

  if (!shown || !pos) return null
  const up = pos.side === 'up' ? '-up' : ''
  return createPortal(
    <div
      ref={ref}
      className={open ? `popin${up}` : `popout${up}`}
      onAnimationEnd={(e) => e.target === e.currentTarget && !open && onExited()}
      style={{
        position: 'fixed',
        zIndex: 50,
        background: color.benchRaised,
        border: `1px solid ${color.benchLineStrong}`,
        borderRadius: radius.lg,
        boxShadow: '0 12px 32px rgba(0,0,0,0.45)',
        display: 'flex',
        flexDirection: 'column',
        ...pos.style,
      }}
    >
      {children}
    </div>,
    document.body,
  )
}
