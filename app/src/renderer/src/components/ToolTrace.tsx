import { useState } from 'react'
import { ChevronRight } from 'lucide-react'

import { color, type } from '../theme'

export interface ToolEvent {
  kind: 'call' | 'result'
  name: string
  info: string
}

// Tool calls that change something or produce a report: each keeps a line of its
// own when the rest of the trace folds away.
const KEY_TOOLS = new Set([
  'run_check',
  'run_review',
  'run_erc',
  'run_drc',
  'create_footprint',
  'update_diagram',
  'add_bom_row',
  'update_bom_row',
  'remove_bom_row',
  'merge_bom_rows',
  'keep_bom_row_separate',
  'propose_bom_fill',
  'fill_bom_from_schematic',
])

export interface KeyLine {
  name: string
  done: boolean
  info: string
}

/** What a folded stretch of the trace shows while closed: how many ordinary
 *  steps it hides, and one line per key call with its result. */
export function foldTrace(events: ToolEvent[]): { steps: number; key: KeyLine[] } {
  const key: KeyLine[] = []
  let steps = 0
  for (const ev of events) {
    if (!KEY_TOOLS.has(ev.name)) {
      if (ev.kind === 'call') steps += 1
    } else if (ev.kind === 'call') {
      key.push({ name: ev.name, done: false, info: '' })
    } else {
      const open = key.find((k) => k.name === ev.name && !k.done)
      if (open) Object.assign(open, { done: true, info: ev.info })
      else key.push({ name: ev.name, done: true, info: ev.info })
    }
  }
  return { steps, key }
}

function summary(info: string): string {
  const first = info.split('\n')[0].slice(0, 60)
  return first ? ` — ${first}` : ''
}

function Lines({ events }: { events: ToolEvent[] }) {
  return (
    <>
      {events.map((ev, j) => (
        <div key={j} style={{ color: ev.kind === 'call' ? color.agent : color.inkSecondary }}>
          {ev.kind === 'call' ? '▸ ' : '  ✓ '}
          {ev.name}
          {ev.kind === 'result' ? summary(ev.info) : ''}
        </div>
      ))}
    </>
  )
}

/** The agent's tool calls. The first `folded` events sit behind a "N steps" bar
 *  (key calls keep a line beside it); anything after them is still running and
 *  shows in full until the answer picks up again. */
export function ToolTrace({ tools, folded }: { tools: ToolEvent[]; folded: number }) {
  const [open, setOpen] = useState(false)
  if (tools.length === 0) return null
  const past = tools.slice(0, folded)
  const running = tools.slice(folded)
  const { steps, key } = foldTrace(past)
  return (
    <div style={{ marginTop: 8, fontFamily: type.mono, fontSize: type.size.xs }}>
      {steps > 0 && (
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 0',
            border: 0,
            background: 'transparent',
            color: color.inkMuted,
            fontSize: type.size.xs,
            fontFamily: type.mono,
          }}
        >
          <ChevronRight
            size={13}
            strokeWidth={1.75}
            style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms var(--ease-out)' }}
          />
          {steps} {steps === 1 ? 'step' : 'steps'}
        </button>
      )}
      {open && steps > 0 ? (
        <div style={{ marginLeft: 6, paddingLeft: 10, borderLeft: `1px solid ${color.benchLine}` }}>
          <Lines events={past} />
        </div>
      ) : (
        key.map((k, j) => (
          <div key={j} style={{ color: color.agent }}>
            {k.done ? '✓ ' : '▸ '}
            {k.name}
            <span style={{ color: color.inkSecondary }}>{summary(k.info)}</span>
          </div>
        ))
      )}
      <Lines events={running} />
    </div>
  )
}
