import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { ReviewerRow, ReviewProgress } from '../api'
import { color, type } from '../theme'

const MAX_STEPS = 120

// Fold one review_progress event into the team's rows. Lifecycle events set a
// reviewer's status; step / finding / usage add to its row. Mirrors
// merge_progress in agent/stream.py, which builds the saved transcript.
export function applyProgress(team: ReviewerRow[], ev: ReviewProgress): ReviewerRow[] {
  const prev = team.find((r) => r.reviewer === ev.reviewer)
  let row: ReviewerRow
  if (ev.status === 'started' || ev.status === 'planning') {
    // A (re)start resets the row: a second review in one turn reuses names.
    row = { reviewer: ev.reviewer, focus: ev.focus ?? prev?.focus, status: ev.status, steps: [], t0: Date.now() }
  } else if (ev.status === 'done' || ev.status === 'failed') {
    row = { ...(prev ?? { reviewer: ev.reviewer }), status: ev.status, ms: ev.ms, error: ev.error ?? prev?.error }
    if (ev.findings !== undefined) row.findings = ev.findings
  } else if (ev.status === 'step' || ev.status === 'finding') {
    const base: ReviewerRow = prev ?? { reviewer: ev.reviewer, status: 'started', t0: Date.now() }
    const line = ev.status === 'finding' ? `⚑ ${ev.summary ?? ''}` : (ev.summary ?? '')
    row = {
      ...base,
      steps: [...(base.steps ?? []), line].slice(-MAX_STEPS),
      last: ev.summary ?? base.last,
      findings: ev.status === 'finding' ? ev.findings : base.findings,
    }
  } else {
    row = {
      ...(prev ?? { reviewer: ev.reviewer, status: 'started' }),
      requests: ev.requests,
      tokens_in: ev.tokens_in,
      tokens_cached: ev.tokens_cached,
      tokens_cache_write: ev.tokens_cache_write,
      tokens_out: ev.tokens_out,
    }
  }
  return prev ? team.map((r) => (r.reviewer === ev.reviewer ? row : r)) : [...team, row]
}

// Live view of a team review: one row per reviewer, with what it is doing right
// now. Click a reviewer to see every step it took. Folds to a one-line summary
// once the team finishes.
export function ReviewTeam({ rows, live }: { rows: ReviewerRow[]; live: boolean }) {
  const working = rows.some((r) => r.status === 'started' || r.status === 'planning')
  const [open, setOpen] = useState(working)
  const [expanded, setExpanded] = useState<string | null>(null)
  const now = useTicker(working)

  useEffect(() => {
    if (!working) setOpen(false)
  }, [working])

  const reviewers = rows.filter((r) => r.reviewer !== 'planner')
  const total = reviewers.reduce((n, r) => n + (r.findings ?? 0), 0)
  const failed = reviewers.filter((r) => r.status === 'failed').length
  const longest = Math.max(0, ...reviewers.map((r) => r.ms ?? 0))
  // cache reads are cheap, so the headline counts only uncached input
  const tokens = reviewers.reduce((n, r) => n + (r.tokens_in ?? 0) - (r.tokens_cached ?? 0), 0)

  const summary = working
    ? `review team · ${reviewers.filter((r) => r.status === 'done' || r.status === 'failed').length}/${reviewers.length} done`
    : `review team · ${reviewers.length} reviewer${reviewers.length === 1 ? '' : 's'} · ${total} finding${
        total === 1 ? '' : 's'
      }${failed ? ` · ${failed} failed` : ''} · ${seconds(longest)}${tokens ? ` · ${compact(tokens)} uncached tokens in` : ''}`

  return (
    <div style={{ marginTop: 6, fontFamily: type.mono, fontSize: type.size.xs }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={working && live ? 'breathe' : undefined}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          padding: '2px 0',
          border: 0,
          background: 'transparent',
          color: color.agent,
          fontSize: type.size.xs,
          fontFamily: type.mono,
        }}
      >
        <Chevron open={open} />
        {summary}
      </button>
      {open && (
        <div
          style={{
            marginTop: 2,
            marginLeft: 6,
            paddingLeft: 10,
            borderLeft: `1px solid ${color.agentDim}`,
            display: 'grid',
            gap: 4,
          }}
        >
          {rows.map((r) => (
            <Row
              key={r.reviewer}
              row={r}
              now={now}
              live={live}
              expanded={expanded === r.reviewer}
              onToggle={() => setExpanded((x) => (x === r.reviewer ? null : r.reviewer))}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function Row({
  row,
  now,
  live,
  expanded,
  onToggle,
}: {
  row: ReviewerRow
  now: number
  live: boolean
  expanded: boolean
  onToggle: () => void
}) {
  const busy = row.status === 'started' || row.status === 'planning'
  const stalled = busy && !live // reloaded mid-review: it never finished
  const elapsed = row.ms ?? (row.t0 ? now - row.t0 : 0)
  const steps = row.steps ?? []

  const mark =
    row.status === 'done' ? (
      <span style={{ color: color.statusVerified }}>✓</span>
    ) : row.status === 'failed' || stalled ? (
      <span style={{ color: color.statusCritical }}>✕</span>
    ) : (
      <span className="breathe" style={{ color: color.agent }}>
        ●
      </span>
    )

  const right =
    row.status === 'done'
      ? row.reviewer === 'planner'
        ? 'planned'
        : `${row.findings ?? 0} finding${row.findings === 1 ? '' : 's'}`
      : row.status === 'failed'
        ? 'failed'
        : stalled
          ? 'interrupted'
          : row.status === 'planning'
            ? 'planning…'
            : `${row.findings ? `${row.findings} so far · ` : ''}working…`

  return (
    <div>
      <button
        onClick={onToggle}
        disabled={steps.length === 0}
        aria-expanded={expanded}
        title={row.error || row.focus || undefined}
        style={{
          all: 'unset',
          boxSizing: 'border-box',
          width: '100%',
          display: 'flex',
          alignItems: 'baseline',
          gap: 8,
          minWidth: 0,
          color: color.inkSecondary,
          cursor: steps.length ? 'pointer' : 'default',
        }}
      >
        <span style={{ width: 10, flex: 'none', textAlign: 'center' }}>{mark}</span>
        <span style={{ color: color.inkPrimary, flex: 'none' }}>{row.reviewer}</span>
        <span
          style={{
            color: color.inkMuted,
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {busy && !stalled && row.last ? row.last : row.focus}
        </span>
        <span style={{ flex: 'none', color: row.status === 'failed' ? color.statusCritical : undefined }}>{right}</span>
        <span style={{ flex: 'none', width: 44, textAlign: 'right', color: color.inkMuted }}>
          {elapsed ? seconds(elapsed) : ''}
        </span>
        <span style={{ flex: 'none', width: 12, color: color.inkMuted }}>{steps.length > 0 && <Chevron open={expanded} />}</span>
      </button>
      {expanded && (
        <div
          style={{
            margin: '4px 0 6px 18px',
            paddingLeft: 10,
            borderLeft: `1px solid ${color.benchLine}`,
            display: 'grid',
            gap: 1,
            maxHeight: 240,
            overflow: 'auto',
          }}
        >
          {steps.map((s, i) => (
            <div
              key={i}
              style={{
                color: s.startsWith('⚑') ? color.inkPrimary : s.startsWith('auditing') ? color.agent : color.inkMuted,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={s}
            >
              {s}
            </div>
          ))}
          {row.tokens_in ? (
            <div style={{ color: color.inkMuted, marginTop: 4 }}>
              {row.requests} model calls · {usageLine(row)}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronRight
      size={13}
      strokeWidth={1.75}
      style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms var(--ease-out)' }}
    />
  )
}

function seconds(ms: number): string {
  return ms < 10_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms / 1000)}s`
}

// Input split by how it's billed: fresh, written to the prompt cache, read from it.
export function usageLine(u: { tokens_in?: number; tokens_cached?: number; tokens_cache_write?: number; tokens_out?: number }): string {
  const cached = u.tokens_cached ?? 0
  const written = u.tokens_cache_write ?? 0
  const fresh = Math.max(0, (u.tokens_in ?? 0) - cached - written)
  return `${compact(fresh)} fresh · ${compact(written)} cache write · ${compact(cached)} cache read · ${compact(u.tokens_out ?? 0)} out`
}

export function compact(n: number): string {
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n)
}

// Re-render every 250 ms while reviewers are working, so their timers count up.
function useTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(id)
  }, [active])
  return now
}
