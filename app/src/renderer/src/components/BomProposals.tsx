import { useState } from 'react'
import {
  approveAllBomProposals,
  approveBomProposal,
  rejectBomProposal,
  type BomProposal,
  type Offer,
} from '../api'
import { color, radius, type } from '../theme'
import { active, BulkBadge, CompareGrid, price, stock } from './CompareGrid'

// One pending row: what's on the BOM now → what DigiKey offers, the reason it
// was chosen, a side-by-side compare of the alternatives, and approve / reject.
function ProposalRow({ p, onDone }: { p: BomProposal; onDone: (msg?: string) => void }) {
  const offers = [p.offer, ...(p.alternatives ?? [])].filter(Boolean) as Offer[]
  const [choice, setChoice] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)
  const o = offers[choice]

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    try {
      await fn()
      onDone()
    } catch (e) {
      onDone(String(e))
    } finally {
      setBusy(false)
    }
  }

  const cell = { padding: '8px', verticalAlign: 'top' as const }
  return (
    <>
      <tr style={{ borderBottom: expanded ? 'none' : `1px solid ${color.benchLine}` }}>
        <td style={{ ...cell, fontFamily: type.mono, color: color.inkPrimary }}>
          {(p.refdes ?? []).join(', ')}
          <div style={{ color: color.inkMuted, fontSize: type.size.xs }}>
            {p.qty}× {p.value}
          </div>
        </td>
        <td style={{ ...cell, fontFamily: type.mono }}>
          <span style={{ color: color.inkMuted }}>{p.current_mpn ?? '—'}</span>
          <span style={{ color: color.inkMuted }}> → </span>
          <span style={{ color: color.inkPrimary }}>{o.mpn}</span>
          <div style={{ fontFamily: type.sans, fontSize: type.size.xs, color: color.inkSecondary, marginTop: 2 }}>
            {o.manufacturer}
            {o.packaging ? ` · ${o.packaging}` : ''}
            {o.digikey_pn ? ` · ${o.digikey_pn}` : ''}
            {!active(o) && (
              <span style={{ color: color.statusCaution }}> · {o.lifecycle ?? 'status unknown'}</span>
            )}
          </div>
          <BulkBadge o={o} qty={p.qty ?? 1} />
          <div style={{ display: 'flex', gap: 12, marginTop: 6, fontFamily: type.sans, fontSize: type.size.xs }}>
            {offers.length > 1 && (
              <button
                onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}
                style={{ all: 'unset', cursor: 'pointer', color: color.copper }}
              >
                {expanded ? '▾' : '▸'} Compare {offers.length} options
                {choice > 0 ? <span style={{ color: color.inkMuted }}> · picked Alt {choice}</span> : null}
              </button>
            )}
            {o.product_url && (
              <a href={o.product_url} target="_blank" rel="noreferrer" style={{ color: color.copper }}>
                DigiKey ↗
              </a>
            )}
            {o.datasheet_url && (
              <a href={o.datasheet_url} target="_blank" rel="noreferrer" style={{ color: color.copper }}>
                Datasheet ↗
              </a>
            )}
          </div>
        </td>
        <td style={{ ...cell, textAlign: 'right', fontFamily: type.mono, whiteSpace: 'nowrap' }}>
          <span style={{ color: color.inkMuted }}>{price(p.current_price)}</span>
          <span style={{ color: color.inkMuted }}> → </span>
          <span style={{ color: color.copperBright }}>{price(o.unit_price)}</span>
          <div style={{ fontSize: type.size.xs, color: o.bulk_only ? color.statusCaution : color.inkMuted }}>
            {o.bulk_only && typeof o.min_order_cost === 'number'
              ? `lot ${price(o.min_order_cost)}`
              : `${stock(o.stock)} in stock`}
          </div>
        </td>
        <td style={{ ...cell, fontSize: type.size.xs, color: color.inkSecondary, maxWidth: 280 }}>{p.rationale}</td>
        <td style={{ ...cell, whiteSpace: 'nowrap', textAlign: 'right' }}>
          <button disabled={busy} onClick={() => act(() => approveBomProposal(p.id, choice))}>
            Approve
          </button>{' '}
          <button
            disabled={busy}
            onClick={() => act(() => rejectBomProposal(p.id))}
            style={{ background: 'transparent', color: color.inkSecondary }}
          >
            Reject
          </button>
        </td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: `1px solid ${color.benchLine}` }}>
          <td colSpan={5} style={{ padding: '0 8px 8px' }}>
            <CompareGrid offers={offers} qty={p.qty ?? 1} choice={choice} onPick={setChoice} />
          </td>
        </tr>
      )}
    </>
  )
}

export function BomProposals({
  proposals,
  onChanged,
}: {
  proposals: BomProposal[]
  onChanged: (err?: string) => void
}) {
  const pending = proposals.filter((p) => p.status === 'pending' && p.offer)
  const skipped = proposals.filter((p) => p.status === 'skipped')
  const [showSkipped, setShowSkipped] = useState(false)
  const [busy, setBusy] = useState(false)
  if (pending.length === 0 && skipped.length === 0) return null

  const th = {
    padding: '6px 8px',
    fontWeight: type.weight.med,
    color: color.inkSecondary,
    fontSize: type.size.xs,
    textAlign: 'left' as const,
    borderBottom: `1px solid ${color.benchLineStrong}`,
  }

  return (
    <div
      className="settle"
      style={{
        border: `1px solid ${color.benchLineStrong}`,
        borderLeft: `2px solid ${color.copper}`,
        borderRadius: radius.md,
        background: color.benchRaised,
        padding: '12px 14px',
        marginBottom: 16,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: pending.length ? 10 : 0 }}>
        <span style={{ fontWeight: type.weight.med, color: color.inkPrimary }}>DigiKey proposals</span>
        <span style={{ fontSize: type.size.xs, color: color.inkMuted }}>
          {pending.length} to review · nothing changes until you approve
        </span>
        <span style={{ flex: 1 }} />
        {pending.length > 1 && (
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await approveAllBomProposals()
                onChanged()
              } catch (e) {
                onChanged(String(e))
              } finally {
                setBusy(false)
              }
            }}
          >
            Approve all {pending.length}
          </button>
        )}
      </div>

      {pending.length > 0 && (
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: type.size.sm }}>
          <thead>
            <tr>
              <th style={th}>Refdes</th>
              <th style={th}>Part</th>
              <th style={{ ...th, textAlign: 'right' }}>Unit $</th>
              <th style={th}>Why this part</th>
              <th style={th}></th>
            </tr>
          </thead>
          <tbody>
            {pending.map((p) => (
              <ProposalRow key={`${p.id}-${p.created_at}`} p={p} onDone={onChanged} />
            ))}
          </tbody>
        </table>
      )}

      {skipped.length > 0 && (
        <div style={{ marginTop: pending.length ? 10 : 6, fontSize: type.size.xs }}>
          <button
            onClick={() => setShowSkipped((v) => !v)}
            style={{ all: 'unset', cursor: 'pointer', color: color.inkSecondary }}
            aria-expanded={showSkipped}
          >
            {showSkipped ? '▾' : '▸'} {skipped.length} row{skipped.length === 1 ? '' : 's'} not looked up
          </button>
          {showSkipped && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: color.inkMuted, lineHeight: 1.6 }}>
              {skipped.map((p) => (
                <li key={p.id}>
                  <span style={{ fontFamily: type.mono, color: color.inkSecondary }}>{(p.refdes ?? []).join(', ')}</span>{' '}
                  {p.value ? `(${p.value}) ` : ''}— {p.rationale}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
