import { Maximize, Minus, Plus, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  acceptFootprint,
  footprintPreviewUrl,
  getFootprint,
  rejectFootprint,
  type FootprintDim,
  type FootprintPad,
  type FootprintProposal,
} from '../api'
import { color, radius, type } from '../theme'
import { IconButton } from './DiagramTab'

const fmt = (v: number | null) => (v == null ? '—' : String(v))

// A footprint the agent generated: the drawing, every number with the datasheet
// page it came from, and Accept / Reject. Nothing is written until Accept.
export function FootprintCard({ id }: { id: string }) {
  const [p, setP] = useState<FootprintProposal | null>(null)
  const [svg, setSvg] = useState<string | null>(null)
  const [previewFailed, setPreviewFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const closeZoom = useCallback(() => setZoomed(false), [])

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
        <button
          onClick={() => setZoomed(true)}
          aria-label={`Open ${p.name} drawing to zoom in`}
          title="Click to zoom in"
          style={{
            background: color.benchVoid,
            border: `1px solid ${color.benchLine}`,
            borderRadius: radius.sm,
            padding: 12,
            height: 220,
            cursor: 'zoom-in',
          }}
        >
          {/* the SVG is a few mm wide: stretch it to the box, it's vector */}
          <img
            src={svg}
            alt={`${p.name} footprint drawing`}
            style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
            onError={() => setPreviewFailed(true)}
          />
        </button>
      ) : previewFailed ? (
        <div style={{ fontSize: type.size.xs, color: color.inkMuted }}>
          No preview: kicad-cli couldn't draw it. Check the footprint in KiCad after accepting.
        </div>
      ) : null}

      <DimsTable dims={p.dims} />
      {/* an explicit land pattern has no package dims: its pads are the numbers to check */}
      <PadsTable pads={p.pads ?? []} open={p.dims.length === 0} />
      {zoomed && svg && <ZoomView url={svg} name={p.name} onClose={closeZoom} />}

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

// The lands as built, in footprint coordinates (mm, pad centres).
function PadsTable({ pads, open }: { pads: FootprintPad[]; open: boolean }) {
  if (pads.length === 0) return null
  const cell = { padding: '2px 6px', borderBottom: `1px solid ${color.paperRule}` }
  const drilled = pads.some((d) => d.drill != null)
  return (
    <details open={open} style={{ fontSize: type.size.xs }}>
      <summary style={{ cursor: 'pointer', color: color.inkSecondary }}>Pads as built ({pads.length})</summary>
      <div style={{ overflowX: 'auto', marginTop: 6 }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            background: color.paper,
            color: color.paperInk,
            borderRadius: radius.sm,
            lineHeight: type.line.xs,
          }}
        >
          <thead>
            <tr style={{ color: color.paperInkSoft, textAlign: 'left' }}>
              <th style={cell}>pad</th>
              <th style={cell}>x (mm)</th>
              <th style={cell}>y</th>
              <th style={cell}>width</th>
              <th style={cell}>height</th>
              {drilled && <th style={cell}>drill</th>}
            </tr>
          </thead>
          <tbody style={{ fontFamily: type.mono }}>
            {pads.map((d) => (
              <tr key={d.number}>
                <td style={cell}>{d.number}</td>
                <td style={cell}>{d.x}</td>
                <td style={cell}>{d.y}</td>
                <td style={cell}>{d.w}</td>
                <td style={cell}>{d.h}</td>
                {drilled && <td style={cell}>{fmt(d.drill)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

// kicad-cli sizes the SVG in mm; the browser lays it out at 96 dpi.
const PX_PER_MM = 96 / 25.4
const SCALE_STEPS = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]

// The drawing full-window: wheel zooms about the cursor, drag pans, double-click
// fits, Esc or a click outside closes. The image is resized, not transformed, so
// it stays sharp at any zoom, and a scale bar gives real millimetres.
function ZoomView({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  const frame = useRef<HTMLDivElement>(null)
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null)
  const [nat, setNat] = useState({ w: 0, h: 0 })
  const [view, setView] = useState({ x: 0, y: 0, k: 0 })

  const fit = useCallback(() => {
    const f = frame.current
    if (!f || !nat.w || !nat.h) return
    const k = Math.min((f.clientWidth * 0.85) / nat.w, (f.clientHeight * 0.85) / nat.h)
    setView({ k, x: (f.clientWidth - nat.w * k) / 2, y: (f.clientHeight - nat.h * k) / 2 })
  }, [nat])
  useEffect(fit, [fit])

  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => {
    const f = frame.current
    const px = cx ?? (f ? f.clientWidth / 2 : 0)
    const py = cy ?? (f ? f.clientHeight / 2 : 0)
    setView((v) => {
      const k = Math.min(600, Math.max(1, v.k * factor))
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k }
    })
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    const f = frame.current
    if (!f) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = f.getBoundingClientRect()
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top)
    }
    f.addEventListener('wheel', onWheel, { passive: false })
    return () => f.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const pxPerMm = PX_PER_MM * view.k
  const bar = SCALE_STEPS.find((mm) => mm * pxPerMm >= 60) ?? SCALE_STEPS[SCALE_STEPS.length - 1]

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${name} footprint drawing`}
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: 32,
        background: 'rgba(10, 15, 13, 0.9)',
      }}
    >
      <div
        ref={frame}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={fit}
        onPointerDown={(e) => {
          if (e.button !== 0) return
          e.currentTarget.setPointerCapture(e.pointerId)
          drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y }
        }}
        onPointerMove={(e) => {
          const d = drag.current
          if (d) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }))
        }}
        onPointerUp={() => (drag.current = null)}
        style={{
          position: 'relative',
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          background: color.benchVoid,
          border: `1px solid ${color.benchLineStrong}`,
          borderRadius: radius.md,
          cursor: 'grab',
          touchAction: 'none',
        }}
      >
        <img
          src={url}
          alt={`${name} footprint drawing`}
          draggable={false}
          onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          style={{
            position: 'absolute',
            left: view.x,
            top: view.y,
            width: nat.w * view.k || undefined,
            height: nat.h * view.k || undefined,
            maxWidth: 'none',
            visibility: view.k ? 'visible' : 'hidden',
            userSelect: 'none',
          }}
        />
        {view.k > 0 && (
          <div
            style={{
              position: 'absolute',
              left: 14,
              bottom: 14,
              fontFamily: type.mono,
              fontSize: type.size.xs,
              color: color.inkSecondary,
              pointerEvents: 'none',
            }}
          >
            <div style={{ width: bar * pxPerMm, height: 5, border: `1px solid ${color.inkSecondary}`, borderTop: 0 }} />
            {bar} mm
          </div>
        )}
        <div
          onPointerDown={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            right: 14,
            bottom: 14,
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            padding: 3,
            background: color.benchRaised,
            border: `1px solid ${color.benchLineStrong}`,
            borderRadius: radius.md,
            cursor: 'default',
          }}
        >
          <IconButton label="Zoom out" onClick={() => zoomAt(1 / 1.25)}>
            <Minus size={15} strokeWidth={1.75} />
          </IconButton>
          <IconButton label="Zoom in" onClick={() => zoomAt(1.25)}>
            <Plus size={15} strokeWidth={1.75} />
          </IconButton>
          <IconButton label="Fit to view (double-click)" onClick={fit}>
            <Maximize size={14} strokeWidth={1.75} />
          </IconButton>
          <span style={{ width: 1, height: 16, background: color.benchLine, margin: '0 3px' }} />
          <IconButton label="Close (Esc)" onClick={onClose}>
            <X size={15} strokeWidth={1.75} />
          </IconButton>
        </div>
      </div>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ fontFamily: type.mono, fontSize: 12.5, color: color.inkSecondary, textAlign: 'center' }}
      >
        {name} · scroll to zoom, drag to pan, double-click to fit
      </div>
    </div>,
    document.body,
  )
}
