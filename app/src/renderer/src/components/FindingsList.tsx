import type { Finding } from '../types/findings'
import { useState } from 'react'
import { datasheetUrl, mute, unmute, type ReviewResponse, type RunInfo } from '../api'
import { color, radius, severity, type, type SeverityKey } from '../theme'
import { relTime } from '../time'
import { SeverityMark, StatNumeral } from './marks'

const sevOf = (s: string): SeverityKey => (s in severity ? (s as SeverityKey) : 'cosmetic')

// Machine identifiers (RefDes, nets, part numbers) render in mono — a real job:
// digit alignment and 0/O 1/l disambiguation.
const Mono = ({ children }: { children: React.ReactNode }) => (
  <span style={{ fontFamily: type.mono, color: color.inkPrimary }}>{children}</span>
)

function EvidenceLine({ f }: { f: Finding }) {
  // Honest source on "paper": the tool's proof material is warm, never bench
  // chrome. A reviewer's datasheet evidence carries the exact quote it read (the
  // gate checked it is on that page) and opens the PDF at that page.
  const quote = f.excerpt ?? null
  const page = f.page ?? null
  const part = f.datasheet ?? null
  return (
    <div
      style={{
        background: color.paper,
        color: color.paperInk,
        border: `1px solid ${color.paperEdge}`,
        borderRadius: radius.sm,
        padding: '7px 10px',
        marginTop: 8,
        fontSize: type.size.sm,
      }}
    >
      {quote && (
        <div style={{ fontStyle: 'italic', lineHeight: type.line.sm, marginBottom: 4 }}>“{quote}”</div>
      )}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, fontFamily: type.mono, flexWrap: 'wrap' }}>
        <span>
          <span style={{ color: color.paperInkSoft }}>source&nbsp;&nbsp;</span>
          {f.citation}
        </span>
        {part && page && (
          <button
            onClick={async () => window.open(await datasheetUrl(part, page), '_blank')}
            style={{
              all: 'unset',
              cursor: 'pointer',
              color: color.copperDim,
              textDecoration: 'underline',
              textUnderlineOffset: 2,
            }}
          >
            open p.{page}
          </button>
        )}
      </div>
    </div>
  )
}

// The card's action: dismiss a current finding, restore a dismissed one, or
// none (an AI finding the last review didn't raise again is history, not a todo).
type CardAction = 'dismiss' | 'restore' | null

function FindingCard({ f, action, onChanged }: { f: Finding; action: CardAction; onChanged: () => void }) {
  const sev = sevOf(f.severity)
  const tone = severity[sev].tone
  return (
    <div
      className="settle"
      style={{
        background: color.benchRaised,
        border: `1px solid ${color.benchLine}`,
        borderLeft: `2px solid ${tone}`,
        borderRadius: radius.md,
        padding: '12px 14px',
        marginBottom: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ paddingTop: 3 }}>
          <SeverityMark sev={sev} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* claim — the hero sentence */}
          <div style={{ fontSize: type.size.md, lineHeight: type.line.md, fontWeight: type.weight.med }}>
            {f.component_ref && <Mono>{f.component_ref}</Mono>}
            {f.component_ref ? '  ' : ''}
            {f.title}
          </div>
          {/* explanation — the marker carries urgency, not the words */}
          {(f.failure_mode || f.detail) && (
            <div style={{ fontSize: type.size.sm, lineHeight: type.line.sm, color: color.inkSecondary, marginTop: 5 }}>
              {f.failure_mode || f.detail}
            </div>
          )}
          {f.citation && <EvidenceLine f={f} />}
          {/* location — where on their board */}
          <div style={{ fontSize: type.size.xs, color: color.inkMuted, marginTop: 8, fontFamily: type.mono }}>
            {[
              severity[sev].label,
              f.net ? `net ${f.net}` : null,
              f.component_ref,
              `${f.source}${f.pattern_status ? `/${f.pattern_status}` : ''}`,
              f.connectivity_verified !== 'not-applicable' ? f.connectivity_verified : null,
              ...(f.labels ?? []).map((l) => (l.startsWith('reviewer:') ? `reviewer ${l.slice(9)}` : l)),
            ]
              .filter(Boolean)
              .join('  ·  ')}
          </div>
        </div>
        {action && (
          <button
            onClick={async () => {
              await (action === 'dismiss' ? mute(f.mute_key) : unmute(f.mute_key))
              onChanged()
            }}
            title={action === 'dismiss' ? 'dismiss this finding' : 'show this finding again'}
          >
            {action === 'dismiss' ? 'Dismiss' : 'Restore'}
          </button>
        )}
      </div>
    </div>
  )
}

// One line per kind of run: when it last ran, and whether the schematic has
// changed since (then its findings may already be fixed, or out of date).
function RunLine({ label, run }: { label: string; run: RunInfo }) {
  return (
    <div style={{ fontFamily: type.mono, fontSize: type.size.xs, color: color.inkMuted }}>
      {[label, run.model ? run.model.split(':').pop() : null, relTime(run.ts)].filter(Boolean).join('  ·  ')}
      {run.stale && <span style={{ color: color.statusCaution }}>{'  ·  '}schematic changed since</span>}
    </div>
  )
}

// What ran, when, and what it covered — so an empty result reads as "checked and
// clean", not "nothing happened".
function ReviewReceipt({ review }: { review: ReviewResponse }) {
  const { rules, review: ai } = review.runs
  const failed = (ref: string) =>
    review.findings.filter((f) => f.source === 'pattern' && f.component_ref === ref).length
  const checks = review.coverage.reduce((n, c) => n + c.checks, 0)
  const passed = review.coverage.reduce((n, c) => n + Math.max(0, c.checks - failed(c.ref)), 0)
  const covered = new Set(review.coverage.map((c) => c.ref)).size
  const uncovered = Math.max(0, review.components - covered)

  return (
    <div style={{ marginBottom: 16, display: 'grid', gap: 2 }}>
      {rules && <RunLine label="rule checks" run={rules} />}
      {ai && <RunLine label="AI review" run={ai} />}
      {review.coverage.length > 0 && (
        <>
          <div style={{ fontSize: type.size.sm, color: color.inkSecondary, marginTop: 6 }}>
            {passed}/{checks} pattern checks passed across {covered} part{covered === 1 ? '' : 's'}
            {uncovered > 0 &&
              (ai
                ? ` · the other ${uncovered} were covered by the AI review only`
                : ` · the other ${uncovered} have no pattern — ask in chat for a design review`)}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            {review.coverage.map((c) => {
              const bad = failed(c.ref)
              return (
                <span
                  key={`${c.ref}-${c.part}`}
                  title={c.part}
                  style={{
                    fontFamily: type.mono,
                    fontSize: type.size.xs,
                    padding: '2px 7px',
                    borderRadius: radius.sm,
                    border: `1px solid ${bad ? color.statusCaution : color.benchLine}`,
                    color: bad ? color.statusCaution : color.inkSecondary,
                    background: color.benchRaised,
                  }}
                >
                  <span style={{ color: color.inkPrimary }}>{c.ref}</span> {c.part.split(' ')[0]}{' '}
                  <span style={{ color: bad ? color.statusCaution : color.statusVerified }}>
                    {c.checks - Math.min(bad, c.checks)}/{c.checks}
                  </span>
                </span>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

// A collapsed list under the current findings: dismissed ones (restorable) or
// AI findings the last review didn't raise again.
function Drawer({
  title,
  hint,
  findings,
  action,
  onChanged,
}: {
  title: string
  hint: string
  findings: Finding[]
  action: CardAction
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  if (findings.length === 0) return null
  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${color.benchLine}`, paddingTop: 12 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={{ all: 'unset', cursor: 'pointer', fontSize: type.size.sm, color: color.inkSecondary }}
      >
        {open ? '▾' : '▸'} {title} ({findings.length})
      </button>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: type.size.xs, color: color.inkMuted, marginBottom: 10 }}>{hint}</div>
          <div style={{ opacity: 0.75 }}>
            {findings.map((f) => (
              <FindingCard key={f.mute_key} f={f} action={action} onChanged={onChanged} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function FindingsList({ review, onChanged }: { review: ReviewResponse; onChanged: () => void }) {
  const { findings, dismissed, dropped } = review
  const count = (s: string) => findings.filter((f) => f.severity === s).length

  const drawers = (
    <>
      <Drawer
        title="Not raised in this review"
        hint="AI findings from the previous review that this one didn't report. Maybe fixed, maybe just not looked at this time."
        findings={dropped}
        action={null}
        onChanged={onChanged}
      />
      <Drawer
        title="Dismissed"
        hint="Hidden from the findings and from future runs until restored."
        findings={dismissed}
        action="restore"
        onChanged={onChanged}
      />
    </>
  )

  if (findings.length === 0) {
    return (
      <div className="settle">
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '14px 16px',
            marginBottom: 16,
            background: color.benchRaised,
            border: `1px solid ${color.benchLine}`,
            borderLeft: `2px solid ${color.statusVerified}`,
            borderRadius: radius.md,
          }}
        >
          <SeverityMark sev="cosmetic" size={14} />
          <div>
            <div style={{ fontSize: type.size.md, fontWeight: type.weight.med, color: color.inkPrimary }}>
              No issues found
            </div>
            <div style={{ fontSize: type.size.xs, color: color.inkMuted, marginTop: 2 }}>
              {dismissed.length
                ? `${dismissed.length} dismissed finding${dismissed.length === 1 ? '' : 's'} hidden`
                : 'Nothing to fix or verify.'}
            </div>
          </div>
        </div>
        <ReviewReceipt review={review} />
        {drawers}
      </div>
    )
  }

  return (
    <div>
      <ReviewReceipt review={review} />
      {/* summary — a test report header, not a chat */}
      <div
        style={{
          display: 'flex',
          gap: 28,
          alignItems: 'flex-end',
          padding: '4px 0 16px',
          borderBottom: `1px solid ${color.benchLine}`,
          marginBottom: 16,
        }}
      >
        <StatNumeral value={count('must-fix')} label="must-fix" tone={color.statusCritical} />
        <StatNumeral value={count('verify')} label="to verify" tone={color.statusCaution} />
        <StatNumeral value={count('cosmetic')} label="cosmetic" tone={color.inkSecondary} />
      </div>
      {findings.map((f) => (
        <FindingCard key={f.mute_key} f={f} action="dismiss" onChanged={onChanged} />
      ))}
      {drawers}
    </div>
  )
}
