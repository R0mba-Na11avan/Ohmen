import { useEffect, useState } from 'react'
import {
  acceptFootprint,
  footprintPreviewUrl,
  getFootprint,
  rejectFootprint,
  type FootprintDim,
  type FootprintProposal,
} from '../api'
import { color, radius, type } from '../theme'

const fmt = (v: number | null) => (v == null ? '—' : String(v))

// A footprint the agent generated: the drawing, every number with the datasheet
// page it came from, and Accept / Reject. Nothing is written until Accept.
export function FootprintCard({ id }: { id: string }) {
  const [p, setP] = useState<FootprintProposal | null>(null)
  const [svg, setSvg] = useState<string | null>(null)
  const [previewFailed, setPreviewFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    getFootprint(id).then(
      (x) => live && setP(x),
      () => live && setError('This footprint proposal is gone (another project, or deleted).'),
    )
    footprintPreviewUrl(id).then((u) => live && setSvg(u))
    return () => {
      live = false
    }
  }, [id])

  async function decide(accept: boolean) {
    setBusy(true)
    setError(null)
    try {
      setP(await (accept ? acceptFootprint(id) : rejectFootprint(id)))
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setBusy(false)
    }
  }

  if (!p) {
    return error ? <div style={{ marginTop: 8, fontSize: type.size.xs, color: color.inkMuted }}>{error}</div> : null
  }
  return (
    <div
      style={{
        marginTop: 8,
        padding: 10,
        width: 460,
        maxWidth: '100%',
        background: color.benchRaised,
        border: `1px solid ${p.status === 'pending' ? color.copperDim : color.benchLineStrong}`,
        borderRadius: radius.md,
        display: 'grid',
        gap: 8,
        fontSize: type.size.sm,
        lineHeight: type.line.sm,
      }}
    >
      <div>
        <div style={{ fontFamily: type.mono, color: color.inkPrimary, overflowWrap: 'anywhere' }}>{p.name}</div>
        <div style={{ fontSize: type.size.xs, color: color.inkSecondary }}>
          {p.package} for {p.part}
          {p.drawing ? ` · ${p.drawing}` : ''} · IPC-7351B nominal
        </div>
      </div>

      {svg && !previewFailed ? (
        <div
          style={{
            background: color.benchVoid,
            border: `1px solid ${color.benchLine}`,
            borderRadius: radius.sm,
            padding: 8,
            display: 'flex',
            justifyContent: 'center',
          }}
        >
          <img
            src={svg}
            alt={`${p.name} footprint drawing`}
            style={{ maxWidth: '100%', maxHeight: 260 }}
            onError={() => setPreviewFailed(true)}
          />
        </div>
      ) : previewFailed ? (
        <div style={{ fontSize: type.size.xs, color: color.inkMuted }}>
          No preview: kicad-cli couldn't draw it. Check the footprint in KiCad after accepting.
        </div>
      ) : null}

      <DimsTable dims={p.dims} />

      {p.warnings.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 16, color: color.statusCaution, fontSize: type.size.xs }}>
          {p.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      {p.stock.length > 0 && (
        <div style={{ fontSize: type.size.xs, color: color.inkSecondary }}>
          Already in KiCad's library: <span style={{ fontFamily: type.mono }}>{p.stock.join(', ')}</span>
        </div>
      )}

      {p.status === 'pending' ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button
            disabled={busy}
            onClick={() => decide(true)}
            style={{ borderColor: color.copper, color: color.copperBright }}
          >
            Accept
          </button>
          <button disabled={busy} onClick={() => decide(false)}>
            Reject
          </button>
          <span style={{ fontSize: type.size.xs, color: color.inkMuted }}>
            Check each number against the datasheet first.
          </span>
        </div>
      ) : p.status === 'accepted' ? (
        <div style={{ fontSize: type.size.xs, color: color.statusVerified }}>
          Added to {p.file} as <span style={{ fontFamily: type.mono }}>ohmen:{p.name}</span>. Assign it on the
          symbol in KiCad.
        </div>
      ) : (
        <div style={{ fontSize: type.size.xs, color: color.inkMuted }}>Rejected. Nothing was written.</div>
      )}
      {error && <div style={{ fontSize: type.size.xs, color: color.statusCritical }}>{error}</div>}
    </div>
  )
}

// The numbers as read off the drawing, on datasheet paper: they are evidence.
function DimsTable({ dims }: { dims: FootprintDim[] }) {
  if (dims.length === 0) return null
  const cell = { padding: '2px 6px', borderBottom: `1px solid ${color.paperRule}` }
  return (
    <div style={{ overflowX: 'auto' }}>
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          background: color.paper,
          color: color.paperInk,
          borderRadius: radius.sm,
          fontSize: type.size.xs,
          lineHeight: type.line.xs,
        }}
      >
        <thead>
          <tr style={{ color: color.paperInkSoft, textAlign: 'left' }}>
            <th style={cell}>dimension (mm)</th>
            <th style={cell}>min</th>
            <th style={cell}>nom</th>
            <th style={cell}>max</th>
            <th style={cell}>source</th>
          </tr>
        </thead>
        <tbody style={{ fontFamily: type.mono }}>
          {dims.map((d) => (
            <tr key={d.name}>
              <td style={cell}>{d.name.replace(/_/g, ' ')}</td>
              <td style={cell}>{fmt(d.min)}</td>
              <td style={cell}>{fmt(d.nom)}</td>
              <td style={cell}>{fmt(d.max)}</td>
              <td style={{ ...cell, fontFamily: type.sans, color: d.source ? color.paperInk : color.statusCritical }}>
                {d.source || 'not cited'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
