import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import {
  addBomRow,
  deleteBomRow,
  downloadBomCsv,
  fillBom,
  getBom,
  getBomProposals,
  keepBomRowSeparate,
  mergeBomRows,
  proposeBomFill,
  updateBomRow,
  type BomProposal,
  type BomRow,
} from '../api'
import { BomProposals } from './BomProposals'
import { color, radius, space, type } from '../theme'

// Nearest scrolling ancestor — the IntersectionObserver root for the total bar.
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let n = el?.parentElement ?? null
  while (n) {
    if (/(auto|scroll)/.test(getComputedStyle(n).overflowY)) return n
    n = n.parentElement
  }
  return null
}

// Row actions live behind a three-dot menu — no icon clutter per row.
function RowMenu({
  status,
  onPin,
  onReject,
  onDelete,
}: {
  status: string
  onPin: () => void
  onReject: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const run = (fn: () => void) => () => {
    fn()
    setOpen(false)
  }
  const item = {
    display: 'block',
    width: '100%',
    textAlign: 'left' as const,
    background: 'transparent',
    border: 0,
    borderRadius: radius.sm,
    padding: '6px 10px',
    fontSize: type.size.sm,
    color: color.inkPrimary,
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        aria-label="Row actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ padding: '3px 6px', display: 'inline-flex', alignItems: 'center', background: 'transparent', border: 0, color: color.inkSecondary }}
      >
        <MoreHorizontal size={16} strokeWidth={1.75} />
      </button>
      {open && (
        <div
          role="menu"
          className="popin"
          style={{
            position: 'absolute',
            right: 0,
            top: '100%',
            marginTop: 4,
            minWidth: 132,
            background: color.benchRaised,
            border: `1px solid ${color.benchLineStrong}`,
            borderRadius: radius.md,
            boxShadow: '0 8px 32px rgba(0,0,0,0.45)',
            padding: 4,
            zIndex: 10,
          }}
        >
          <button role="menuitem" style={item} onClick={run(onPin)}>
            {status === 'pinned' ? 'Unpin' : 'Pin'}
          </button>
          <button role="menuitem" style={item} onClick={run(onReject)}>
            {status === 'rejected' ? 'Restore' : 'Reject'}
          </button>
          <button role="menuitem" style={{ ...item, color: color.statusCritical }} onClick={run(onDelete)}>
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

const refRange = (refs: string[]) =>
  refs.length > 3 ? `${refs[0]}…${refs[refs.length - 1]}` : refs.join(', ')

// A planned (copilot/manual) row: not placed yet, or maybe already one of the
// schematic rows the fill couldn't choose between. Merging settles it.
function PlannedTag({
  row,
  rows,
  onMerge,
  onKeep,
}: {
  row: BomRow
  rows: BomRow[]
  onMerge: (into: string) => void
  onKeep: () => void
}) {
  const note = { fontFamily: type.sans, fontSize: type.size.xs, whiteSpace: 'nowrap' as const, marginTop: 2 }
  const maybe = (row.duplicate_of ?? [])
    .map((id) => rows.find((x) => x.id === id))
    .filter((x): x is BomRow => !!x)
  if (maybe.length === 0)
    return (
      <div style={{ ...note, color: color.inkMuted }} title="Added before it was placed. Fill from schematic merges it once the schematic has it.">
        planned · not in schematic
      </div>
    )
  return (
    <div style={{ ...note, color: color.statusCaution }}>
      maybe already on the board
      {maybe.map((d) => (
        <button
          key={d.id}
          onClick={() => onMerge(d.id)}
          title={`Fold this row into ${(d.refdes ?? []).join(', ')}: they keep their refdes and qty, and take this row's part details`}
          style={{ display: 'block', marginTop: 4, padding: '2px 8px', fontSize: type.size.xs }}
        >
          Merge into {refRange(d.refdes ?? [])}
        </button>
      ))}
      <button
        onClick={onKeep}
        title="It's a different part: keep this row and stop suggesting these"
        style={{ display: 'block', marginTop: 4, padding: '2px 8px', fontSize: type.size.xs, background: 'transparent', color: color.inkSecondary }}
      >
        Keep separate
      </button>
    </div>
  )
}

const STATUS_COLOR: Record<string, string> = {
  active: color.inkPrimary,
  pinned: color.statusVerified,
  rejected: color.inkMuted,
}

// KiCad identifiers (`USB_C_Receptacle_USB2.0_14P`, `Lib:Footprint_Name`) have no
// spaces, so the column can't shrink below the longest one. Offer line breaks
// after the natural separators instead.
function breakable(s: string | null | undefined) {
  if (!s) return '—'
  return s.split(/(?<=[_:-])/).map((part, i) => (
    <span key={i}>
      {i > 0 && <wbr />}
      {part}
    </span>
  ))
}

const money = (n: number) => `$${n.toFixed(2)}`
const lineTotal = (r: BomRow): number | null =>
  typeof r.line_total === 'number' ? r.line_total : null

// Cost weighting: a row that dominates board cost is flagged so the eye lands on
// what to attack. Major (≥25% of board) = caution; heavy (≥10%) = copper.
function contribTone(share: number): { color: string; weight: number } {
  if (share >= 0.25) return { color: color.statusCaution, weight: type.weight.semi }
  if (share >= 0.1) return { color: color.copper, weight: type.weight.med }
  return { color: color.inkPrimary, weight: type.weight.reg }
}

// The BOM artifact: shared project state. Filled deterministically from the
// schematic or edited by the agent (chat); refetched when `refreshKey` bumps.
export function BomTable({ refreshKey, onCount }: { refreshKey: number; onCount?: (n: number) => void }) {
  const [rows, setRows] = useState<BomRow[]>([])
  const [busy, setBusy] = useState(false)
  const [proposals, setProposals] = useState<BomProposal[]>([])
  const [asking, setAsking] = useState(false)
  const [err, setErr] = useState('')
  const [draft, setDraft] = useState({ mpn: '', value: '', footprint: '' })
  const [flashId, setFlashId] = useState<string | null>(null)
  const [floating, setFloating] = useState(true)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const [headerStuck, setHeaderStuck] = useState(false)
  const headSentinelRef = useRef<HTMLDivElement>(null)

  // The column header stays pinned in place; once rows scroll under it (sentinel
  // above the table has left the scrollport) it takes a raised fill for contrast.
  useEffect(() => {
    const el = headSentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      ([entry]) =>
        setHeaderStuck(
          !entry.isIntersecting && entry.boundingClientRect.top < (entry.rootBounds?.top ?? 0),
        ),
      { root: scrollParent(el), threshold: 0 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [rows.length])

  // The total bar floats over the rows as a distinct widget; once the end of the
  // list scrolls into view (sentinel visible) it settles flush into the bottom.
  // Docking drops the bar's 24px vertical margin, pulling the sentinel up; the
  // root is extended below by more than that so the shift can't flip it back.
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      ([entry]) => setFloating(!entry.isIntersecting),
      { root: scrollParent(el), rootMargin: '0px 0px 32px 0px', threshold: 0 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [rows.length])

  function flash(id: string) {
    setFlashId(id)
    setTimeout(() => setFlashId((cur) => (cur === id ? null : cur)), 700)
  }

  // A single-row change confirms itself with a flash (see .rowflash).
  async function mutate(id: string, fields: Partial<BomRow>) {
    await updateBomRow(id, fields)
    await load()
    flash(id)
  }

  async function load() {
    try {
      const [bom, props] = await Promise.all([getBom(), getBomProposals()])
      setRows(bom)
      setProposals(props)
      setErr('')
    } catch (e) {
      setErr(String(e))
    }
  }
  useEffect(() => {
    load()
  }, [refreshKey])
  // The tab label's count follows edits made here (fill, merge, remove).
  useEffect(() => {
    onCount?.(rows.length)
  }, [rows.length, onCount])

  async function fill() {
    setBusy(true)
    setErr('')
    try {
      setRows(await fillBom())
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  // DigiKey lookups only propose; the panel above the table approves.
  async function askDigikey() {
    setAsking(true)
    setErr('')
    try {
      setProposals(await proposeBomFill())
    } catch (e) {
      setErr(String(e))
    } finally {
      setAsking(false)
    }
  }

  async function merge(source: string, into: string) {
    try {
      await mergeBomRows(source, into)
      await load()
      flash(into)
    } catch (e) {
      setErr(String(e))
    }
  }

  // A part the schematic already places merges into its row; the server returns that row.
  async function add() {
    if (!draft.mpn && !draft.value) return
    const row = await addBomRow({ ...draft, source: 'manual' })
    setDraft({ mpn: '', value: '', footprint: '' })
    await load()
    flash(row.id)
  }

  const boardTotal = rows.reduce((s, r) => s + (lineTotal(r) ?? 0), 0)
  const priced = rows.some((r) => lineTotal(r) !== null)

  const th = {
    padding: '8px',
    fontWeight: type.weight.med,
    color: color.inkSecondary,
    fontSize: type.size.xs,
    position: 'sticky' as const,
    // Offset by the scroll container's top padding so the header pins flush to
    // the pane edge — no strip where rows show through above it.
    top: -space[5],
    zIndex: 1,
    background: headerStuck ? color.benchRaised : color.benchVoid,
    borderBottom: `1px solid ${color.benchLineStrong}`,
    transition: 'background 200ms var(--ease-out)',
  }
  const thR = { ...th, textAlign: 'right' as const }
  const td = { padding: '7px 8px', verticalAlign: 'top' as const }
  const tdR = { ...td, textAlign: 'right' as const, fontFamily: type.mono }

  return (
    // Cancel the scroll container's bottom padding so the docked total bar
    // ends flush with the pane edge at max scroll.
    <div style={{ marginBottom: -space[7] }}>
      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
          border: `1px solid ${color.benchLine}`,
          borderRadius: radius.md,
          padding: '10px 12px',
          background: color.benchRaised,
        }}
      >
        <button onClick={fill} disabled={busy}>
          {busy ? 'Filling…' : 'Fill from schematic'}
        </button>
        <button onClick={askDigikey} disabled={asking || rows.length === 0} title="Look up active rows without a part link on DigiKey; you approve each change">
          {asking ? 'Asking DigiKey…' : 'Fill from DigiKey'}
        </button>
        <span style={{ width: 1, height: 20, background: color.benchLine }} />
        <input placeholder="MPN" value={draft.mpn} onChange={(e) => setDraft({ ...draft, mpn: e.target.value })} style={{ width: 130, fontFamily: type.mono }} />
        <input placeholder="value" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} style={{ width: 90 }} />
        <input placeholder="footprint" value={draft.footprint} onChange={(e) => setDraft({ ...draft, footprint: e.target.value })} style={{ width: 130, fontFamily: type.mono }} />
        <button onClick={add}>Add row</button>
        <span style={{ flex: 1 }} />
        <button onClick={() => downloadBomCsv().catch((e) => setErr(String(e)))} disabled={rows.length === 0}>
          Export CSV
        </button>
      </div>
      {err && <pre style={{ color: color.statusCritical, whiteSpace: 'pre-wrap' }}>{err}</pre>}
      <BomProposals
        proposals={proposals}
        onChanged={(e) => {
          if (e) setErr(e)
          load()
        }}
      />
      {rows.length > 0 && (
        <p style={{ color: color.inkMuted, fontSize: type.size.xs, margin: '0 0 12px' }}>
          Planned rows (added by you or the copilot) merge into their schematic row once it's placed and
          you fill from the schematic. Line $ = qty × unit $; rejected rows don't count.{' '}
          <span style={{ color: color.statusCaution }}>Caution</span> = ≥25% of board cost,{' '}
          <span style={{ color: color.copper }}>copper</span> = ≥10%.
        </p>
      )}
      {rows.length === 0 ? (
        <p style={{ color: color.inkMuted }}>BOM is empty. Fill it from the schematic, or ask the copilot.</p>
      ) : (
        <>
        <div ref={headSentinelRef} style={{ height: 1 }} aria-hidden />
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: type.size.sm }}>
          <thead>
            <tr style={{ textAlign: 'left' }}>
              <th style={th}>Refdes</th>
              <th style={thR}>Qty</th>
              <th style={thR}>Unit&nbsp;$</th>
              <th style={thR}>Line&nbsp;$</th>
              <th style={th}>Value</th>
              <th style={th}>Footprint</th>
              <th style={th}>MPN</th>
              <th style={th}>Why</th>
              <th style={th}>Datasheet</th>
              <th style={th}>Part link</th>
              <th style={th}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const lt = lineTotal(r)
              const share = boardTotal > 0 && lt ? lt / boardTotal : 0
              const tone = contribTone(share)
              return (
                <tr
                  key={r.id}
                  className={flashId === r.id ? 'rowflash' : undefined}
                  style={{
                    borderBottom: `1px solid ${color.benchLine}`,
                    color: STATUS_COLOR[r.status ?? 'active'],
                    textDecoration: r.status === 'rejected' ? 'line-through' : 'none',
                  }}
                >
                  <td style={{ ...td, fontFamily: type.mono }}>
                    {(r.refdes ?? []).join(', ') || '—'}
                    {r.source !== 'schematic' && r.status !== 'rejected' && (
                      <PlannedTag
                        row={r}
                        rows={rows}
                        onMerge={(into) => merge(r.id, into)}
                        onKeep={() => keepBomRowSeparate(r.id).then(load).then(() => flash(r.id), (e) => setErr(String(e)))}
                      />
                    )}
                  </td>
                  <td style={tdR}>{r.qty}</td>
                  <td style={{ ...td, textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <input
                      key={r.id}
                      type="number"
                      min="0"
                      step="0.01"
                      defaultValue={r.unit_price ?? ''}
                      placeholder="—"
                      style={{ width: 72, fontFamily: type.mono, textAlign: 'right' }}
                      onBlur={(e) => {
                        const raw = e.target.value.trim()
                        const v = raw === '' ? null : Number(raw)
                        if (v !== (r.unit_price ?? null) && (v === null || !Number.isNaN(v)))
                          mutate(r.id, { unit_price: v })
                      }}
                    />
                  </td>
                  <td style={{ ...tdR, color: lt ? tone.color : color.inkMuted, fontWeight: tone.weight }}>
                    {lt !== null ? money(lt) : '—'}
                  </td>
                  <td style={td}>{breakable(r.value)}</td>
                  <td style={{ ...td, fontFamily: type.mono, color: color.inkSecondary }}>{breakable(r.footprint)}</td>
                  <td style={{ ...td, fontFamily: type.mono }}>
                    {r.mpn ?? '—'}
                    {(r.manufacturer || r.distributor_pn) && (
                      <div style={{ fontFamily: type.sans, fontSize: type.size.xs, color: color.inkMuted }}>
                        {[r.manufacturer, r.distributor_pn].filter(Boolean).join(' · ')}
                      </div>
                    )}
                    {typeof r.min_order === 'number' && r.min_order > (r.qty ?? 1) && (
                      <div
                        style={{ fontFamily: type.sans, fontSize: type.size.xs, color: color.statusCaution }}
                        title="Not sold in fewer — the line total is the whole lot"
                      >
                        Bulk only · lots of {r.min_order.toLocaleString()}
                      </div>
                    )}
                    {r.lifecycle && r.lifecycle.toLowerCase() !== 'active' && (
                      <div style={{ fontFamily: type.sans, fontSize: type.size.xs, color: color.statusCaution }}>
                        {r.lifecycle}
                      </div>
                    )}
                  </td>
                  <td style={{ ...td, maxWidth: 260, whiteSpace: 'pre-wrap', color: color.inkSecondary }}>{r.why}</td>
                  <td style={td}>
                    {r.datasheet_url ? (
                      <a href={r.datasheet_url} target="_blank" rel="noreferrer" style={{ color: color.copper }}>PDF</a>
                    ) : (
                      <span style={{ color: color.inkMuted }}>—</span>
                    )}
                  </td>
                  <td style={{ ...td, whiteSpace: 'nowrap' }}>
                    <input
                      key={r.id}
                      defaultValue={r.product_url ?? ''}
                      placeholder="DigiKey/… URL"
                      style={{ width: 130 }}
                      onBlur={(e) => {
                        const v = e.target.value.trim()
                        if (v !== (r.product_url ?? '')) mutate(r.id, { product_url: v || null })
                      }}
                    />
                    {r.product_url && (
                      <a href={r.product_url} target="_blank" rel="noreferrer" style={{ marginLeft: 4, color: color.copper }}>↗</a>
                    )}
                  </td>
                  <td style={{ ...td, whiteSpace: 'nowrap', textAlign: 'right' }}>
                    <RowMenu
                      status={r.status ?? 'active'}
                      onPin={() => mutate(r.id, { status: r.status === 'pinned' ? 'active' : 'pinned' })}
                      onReject={() => mutate(r.id, { status: r.status === 'rejected' ? 'active' : 'rejected' })}
                      onDelete={() => deleteBomRow(r.id).then(load)}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {/* Board total — floating widget over the rows, settling flush at the end. */}
        <div
          style={{
            position: 'sticky',
            // Offset past the scroll container's bottom padding so the floating bar
            // hovers just above the pane edge instead of a row-height up.
            bottom: -space[7] + 10,
            zIndex: 5,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '10px 14px',
            background: floating ? color.benchRaised : color.benchBase,
            border: floating ? `1px solid ${color.benchLineStrong}` : '1px solid transparent',
            borderTop: `1px solid ${color.benchLineStrong}`,
            borderRadius: floating ? radius.md : 0,
            boxShadow: floating ? '0 8px 32px rgba(0,0,0,0.45)' : '0 0 0 rgba(0,0,0,0)',
            margin: floating ? '10px 8px 14px' : '0 -24px',
            transition:
              'box-shadow 200ms var(--ease-out), border-radius 200ms var(--ease-out), margin 200ms var(--ease-out), background 200ms var(--ease-out), border-color 200ms var(--ease-out)',
          }}
        >
          <span style={{ color: color.inkSecondary, fontWeight: type.weight.med, fontSize: type.size.sm }}>
            Board total
          </span>
          {!priced && (
            <span style={{ color: color.inkMuted, fontSize: type.size.xs }}>fill unit prices to total the board</span>
          )}
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: type.mono, color: color.copperBright, fontWeight: type.weight.semi, fontSize: type.size.lg }}>
            {priced ? money(boardTotal) : '—'}
          </span>
        </div>
        <div ref={sentinelRef} style={{ height: 1 }} aria-hidden />
        </>
      )}
    </div>
  )
}
