import { useState, type CSSProperties, type ReactNode } from 'react'
import type { Offer } from '../api'
import { color, radius, type } from '../theme'

export const price = (n: number | null | undefined) => (typeof n === 'number' ? `$${n.toFixed(n < 1 ? 3 : 2)}` : '—')
export const stock = (n: number | null | undefined) => (typeof n === 'number' ? n.toLocaleString() : '—')
export const active = (o: Offer) => (o.lifecycle ?? '').toLowerCase() === 'active'

// A part you can't buy just a few of — a solo builder pays for the whole lot.
export function BulkBadge({ o, qty }: { o: Offer; qty: number }) {
  if (!o.bulk_only) return null
  return (
    <div style={{ fontFamily: type.sans, fontSize: type.size.xs, color: color.statusCaution, marginTop: 3 }}>
      Bulk only: sold in lots of {stock(o.min_order)}
      {typeof o.min_order_cost === 'number' ? ` (${price(o.min_order_cost)})` : ''} — you need {qty}
    </div>
  )
}

type Kind = 'resistor' | 'capacitor' | 'other'

// The DigiKey parameters an engineer picks a part of this kind by. The rest
// sit behind "All N params".
const KEY_PARAMS: Record<Kind, string[]> = {
  resistor: ['Resistance', 'Tolerance', 'Power (Watts)', 'Temperature Coefficient', 'Package / Case'],
  capacitor: ['Capacitance', 'Voltage - Rated', 'Temperature Coefficient', 'Package / Case'],
  other: ['Package / Case', 'Supplier Device Package'],
}

function kindOf(offers: Offer[]): Kind {
  const cats = offers.map((o) => (o.category ?? '').toLowerCase()).join(' ')
  const has = (k: string) => offers.some((o) => o.parameters && k in o.parameters)
  if (cats.includes('resistor') || has('Resistance')) return 'resistor'
  if (cats.includes('capacitor') || has('Capacitance')) return 'capacitor'
  return 'other'
}

const param = (o: Offer, name: string) => o.parameters?.[name] ?? ''

type Row = { label: string; text: (o: Offer) => string; render?: (o: Offer) => ReactNode; diff?: boolean }

const differs = (row: Row, offers: Offer[], i: number) =>
  row.diff !== false && i > 0 && row.text(offers[i]).trim() !== row.text(offers[0]).trim()

const rowDiffers = (row: Row, offers: Offer[]) => offers.some((_, i) => differs(row, offers, i))

const paramRow = (name: string): Row => ({ label: name, text: (o) => param(o, name) })

// Side-by-side candidates for one BOM line: specs as rows, offers as columns,
// cells that differ from the proposed part tinted. Picking a column is the
// choice Approve uses.
export function CompareGrid({
  offers,
  qty,
  choice,
  onPick,
}: {
  offers: Offer[]
  qty: number
  choice: number
  onPick: (i: number) => void
}) {
  const [showAll, setShowAll] = useState(false)
  const kind = kindOf(offers)
  const keys = KEY_PARAMS[kind]
  const allNames = [...new Set(offers.flatMap((o) => Object.keys(o.parameters ?? {})))]
  const noSpecs = allNames.length === 0
  const extra = allNames
    .filter((n) => !keys.includes(n))
    .map(paramRow)
    .sort((a, b) => Number(rowDiffers(b, offers)) - Number(rowDiffers(a, offers)))

  const rows: Row[] = [
    { label: 'MPN', text: (o) => o.mpn, render: (o) => <span style={{ fontFamily: type.mono }}>{o.mpn}</span> },
    { label: 'Manufacturer', text: (o) => o.manufacturer ?? '' },
    ...keys.map(paramRow),
    ...(kind === 'other' ? [{ label: 'Description', text: (o: Offer) => o.description ?? '' }] : []),
    { label: 'Packaging', text: (o) => o.packaging ?? '' },
    {
      label: `Unit $ @ ${qty}`,
      text: (o) => price(o.unit_price),
      diff: false,
      render: (o) => (
        <>
          <span style={{ fontFamily: type.mono, color: color.copperBright }}>{price(o.unit_price)}</span>
          <BulkBadge o={o} qty={qty} />
        </>
      ),
    },
    {
      label: 'Stock',
      text: (o) => stock(o.stock),
      diff: false,
      render: (o) => <span style={{ fontFamily: type.mono }}>{stock(o.stock)}</span>,
    },
    ...(offers.some((o) => !active(o))
      ? [
          {
            label: 'Lifecycle',
            text: (o: Offer) => o.lifecycle ?? 'unknown',
            render: (o: Offer) => (
              <span style={{ color: active(o) ? color.inkSecondary : color.statusCaution }}>{o.lifecycle ?? 'unknown'}</span>
            ),
          },
        ]
      : []),
    ...(showAll ? extra : []),
    {
      label: 'Links',
      text: () => '',
      diff: false,
      render: (o) => (
        <span style={{ display: 'inline-flex', gap: 10 }}>
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
          {!o.product_url && !o.datasheet_url && <span style={{ color: color.inkMuted }}>—</span>}
        </span>
      ),
    },
  ]

  const labelCell: CSSProperties = {
    position: 'sticky',
    left: 0,
    background: color.benchBase,
    padding: '5px 10px 5px 8px',
    color: color.inkMuted,
    fontSize: type.size.xs,
    whiteSpace: 'nowrap',
    verticalAlign: 'top',
    borderBottom: `1px solid ${color.benchLine}`,
  }
  const cell = (i: number, tinted: boolean): CSSProperties => ({
    padding: '5px 10px',
    minWidth: 150,
    maxWidth: 220,
    verticalAlign: 'top',
    color: color.inkPrimary,
    fontWeight: tinted ? type.weight.semi : type.weight.reg,
    background: tinted ? DIFF_TINT : i === choice ? color.copperFaint : 'transparent',
    borderLeft: `2px solid ${i === choice ? color.copper : 'transparent'}`,
    borderBottom: `1px solid ${color.benchLine}`,
    overflowWrap: 'anywhere',
  })

  return (
    <div
      style={{
        border: `1px solid ${color.benchLine}`,
        borderRadius: radius.md,
        background: color.benchBase,
        margin: '2px 0 8px',
      }}
    >
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: type.size.sm, fontFamily: type.sans }}>
          <thead>
            <tr>
              <th style={{ ...labelCell, borderBottom: `1px solid ${color.benchLineStrong}` }} />
              {offers.map((o, i) => (
                <th
                  key={`${o.mpn}-${i}`}
                  style={{ ...cell(i, false), textAlign: 'left', fontWeight: type.weight.med, borderBottom: `1px solid ${color.benchLineStrong}` }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Thumb url={o.photo_url} />
                    <div style={{ display: 'grid', gap: 4 }}>
                      <span style={{ fontSize: type.size.xs, color: i === 0 ? color.copperBright : color.inkSecondary }}>
                        {i === 0 ? 'Proposed' : `Alt ${i}`}
                      </span>
                      <button
                        onClick={() => onPick(i)}
                        aria-pressed={i === choice}
                        style={
                          i === choice
                            ? { fontSize: type.size.xs, padding: '2px 8px' }
                            : { fontSize: type.size.xs, padding: '2px 8px', background: 'transparent', color: color.inkSecondary }
                        }
                      >
                        {i === choice ? '● Picked' : 'Pick'}
                      </button>
                    </div>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <td style={labelCell}>{row.label}</td>
                {offers.map((o, i) => {
                  const tinted = differs(row, offers, i)
                  return (
                    <td key={`${o.mpn}-${i}`} style={cell(i, tinted)} title={tinted ? 'Differs from the proposed part' : undefined}>
                      {row.render ? row.render(o) : row.text(o) || <span style={{ color: color.inkMuted }}>—</span>}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 12, padding: '6px 8px', fontSize: type.size.xs, color: color.inkMuted }}>
        {noSpecs ? (
          <span>Specs missing from this saved proposal — run Fill from DigiKey again to compare them.</span>
        ) : (
          extra.length > 0 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              style={{ all: 'unset', cursor: 'pointer', color: color.inkSecondary }}
              aria-expanded={showAll}
            >
              {showAll ? '▾ Key specs only' : `▸ All ${allNames.length} params`}
            </button>
          )
        )}
        <span style={{ flex: 1 }} />
        <span>
          <span style={{ background: DIFF_TINT, padding: '0 4px', borderRadius: radius.sm }}>tinted</span> = differs from proposed
        </span>
      </div>
    </div>
  )
}

// Neutral on purpose: a difference isn't better or worse until the engineer says so.
const DIFF_TINT = 'rgba(228,234,230,0.07)'

function Thumb({ url }: { url?: string | null }) {
  const [broken, setBroken] = useState(false)
  if (!url || broken) return null
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
      style={{ width: 44, height: 44, objectFit: 'contain', background: '#fff', borderRadius: radius.sm, flexShrink: 0 }}
    />
  )
}
