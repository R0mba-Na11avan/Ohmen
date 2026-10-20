import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { EFFORTS, type Effort } from '../effort'
import { color, type } from '../theme'
import { AnchoredPopover } from './Popover'

// The composer's effort chip: how hard the chat model reasons before answering.
// Models that don't reason (or take no setting) get it disabled, with the reason
// in the tooltip, rather than silently ignoring the pick.
export function EffortPicker({
  value,
  onChange,
  disabled,
  model,
  placement,
}: {
  value: Effort
  onChange: (e: Effort) => void
  disabled: boolean
  model?: string
  placement: 'up' | 'down' | 'auto'
}) {
  const [open, setOpen] = useState(false)
  const [closing, setClosing] = useState(false)
  const [active, setActive] = useState(value)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const current = EFFORTS.find((e) => e.id === value) ?? EFFORTS[2]

  const close = useCallback(() => {
    setOpen(false)
    setClosing(!window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  }, [])

  function show() {
    setActive(value)
    setClosing(false)
    setOpen(true)
  }

  useEffect(() => {
    if (open) listRef.current?.focus()
  }, [open])

  function pick(e: Effort) {
    onChange(e)
    close()
    triggerRef.current?.focus()
  }

  function onKey(e: KeyboardEvent) {
    const i = EFFORTS.findIndex((x) => x.id === active)
    const to = (j: number) => {
      e.preventDefault()
      setActive(EFFORTS[(j + EFFORTS.length) % EFFORTS.length].id)
    }
    if (e.key === 'ArrowDown') to(i + 1)
    else if (e.key === 'ArrowUp') to(i - 1)
    else if (e.key === 'Home') to(0)
    else if (e.key === 'End') to(EFFORTS.length - 1)
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      pick(active)
    }
  }

  return (
    <div>
      <button
        ref={triggerRef}
        className="chip"
        onClick={() => (open ? close() : show())}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Effort: ${current.label}`}
        title={disabled ? `${model ?? 'This model'} doesn't take an effort setting` : `Effort: ${current.label}. ${current.hint}`}
        style={{ display: 'flex', alignItems: 'center', gap: 4 }}
      >
        {disabled ? 'Effort' : current.label}
        <ChevronDown size={13} strokeWidth={1.75} style={{ flexShrink: 0 }} />
      </button>
      <AnchoredPopover
        anchorRef={triggerRef}
        open={open}
        closing={closing}
        onClose={close}
        onExited={() => setClosing(false)}
        prefer={placement}
        width={300}
      >
        <div
          ref={listRef}
          role="listbox"
          aria-label="Effort"
          tabIndex={-1}
          aria-activedescendant={`effort-${active}`}
          onKeyDown={onKey}
          style={{ padding: 6, outline: 'none', overflowY: 'auto' }}
        >
          {EFFORTS.map((e) => {
            const selected = e.id === value
            return (
              <div
                key={e.id}
                id={`effort-${e.id}`}
                role="option"
                aria-selected={selected}
                onClick={() => pick(e.id)}
                onMouseEnter={() => setActive(e.id)}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  padding: '6px 8px',
                  borderRadius: 4,
                  cursor: 'pointer',
                  background: selected ? color.copperFaint : e.id === active ? color.benchBase : 'transparent',
                  outline: e.id === active ? `1px solid ${color.benchLine}` : 'none',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: type.size.sm,
                      fontWeight: type.weight.med,
                      color: selected ? color.copperBright : color.inkPrimary,
                    }}
                  >
                    {e.label}
                  </div>
                  <div style={{ fontSize: type.size.xs, color: color.inkMuted, lineHeight: type.line.xs }}>{e.hint}</div>
                </div>
                {selected && <Check size={14} strokeWidth={2} style={{ color: color.copper, marginTop: 2, flexShrink: 0 }} />}
              </div>
            )
          })}
        </div>
      </AnchoredPopover>
    </div>
  )
}
