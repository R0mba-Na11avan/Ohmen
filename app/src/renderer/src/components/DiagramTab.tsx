import { useCallback, useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import ReactMarkdown from 'react-markdown'
import { Maximize, Minus, PanelRightClose, PanelRightOpen, Plus, Zap } from 'lucide-react'
import { diagram, getLastDiagram, revertDiagram, type DiagramResponse } from '../api'
import { color, radius, type } from '../theme'

// Traces on the bench: dark surface, copper edges, mono labels.
mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  themeVariables: {
    darkMode: true,
    background: color.benchBase,
    primaryColor: color.benchRaised,
    primaryBorderColor: color.benchLineStrong,
    primaryTextColor: color.inkPrimary,
    secondaryColor: color.benchRaised,
    tertiaryColor: color.benchBase,
    lineColor: color.copper,
    textColor: color.inkPrimary,
    fontFamily: type.mono,
    fontSize: '13px',
  },
})

// Unique per render: the chat card and the tab can render in the same tick.
let renderSeq = 0
export const renderSvg = (code: string) => mermaid.render(`pcb-diagram-${++renderSeq}`, code).then((r) => r.svg)

// Fixed-height thumbnail for the chat preview card — scaled to fit, no chrome.
export function MermaidView({ code }: { code: string; compact?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    if (!code) return
    let alive = true
    setErr(false)
    renderSvg(code)
      .then((svg) => {
        if (!alive || !ref.current) return
        ref.current.innerHTML = svg
        const el = ref.current.querySelector('svg')
        if (el) {
          el.style.maxWidth = '100%'
          el.style.width = '100%'
          el.style.height = '100%'
        }
      })
      .catch(() => alive && setErr(true))
    return () => {
      alive = false
    }
  }, [code])

  return err ? (
    <div style={{ color: color.inkMuted, fontSize: type.size.xs }}>preview unavailable</div>
  ) : (
    <div ref={ref} style={{ height: 140, overflow: 'hidden', pointerEvents: 'none' }} />
  )
}

// ---------- power-flow marking ----------

// A rail or supply name: +24V, 5V, 3.3V, 3V3, VIN, VBUS, VCC, VDD, VOUT, VSYS…
// Ground is not power flow.
const POWER = /(^|[^a-z0-9])(\+?\d+(\.\d+)?\s?v(\d+)?|v(in|bus|cc|dd|io|out|sys|bat|\+)|pwr|power)(?![a-z])/i
const GROUND = /\b(gnd|ground|vss|0v)\b/i
const isPower = (text: string) => POWER.test(text) && !GROUND.test(text)
// A rail NODE ("+5V Rail", "3V3"): says "rail", or is nothing but a rail name.
// Not any node that mentions a voltage — "U1 buck 24V to 5V" would drag its FB/SW
// edges in.
const RAIL_ONLY = /^\s*(\+?\d+(\.\d+)?\s?v(\d+)?|v(in|bus|cc|dd|io|out|sys|bat))\s*$/i
const isRailNode = (text: string) => isPower(text) && (/\brail\b/i.test(text) || RAIL_ONLY.test(text))

// Mermaid gives an edge's <path> and its label the same data-id (the edge id,
// "L_<from>_<to>_<n>"). An edge is power if its label names a rail, or if it
// runs into/out of a rail node. Paths are drawn
// source -> target, so a dash marching along the path shows the flow direction.
function markPowerEdges(svg: SVGSVGElement) {
  const railNodes = new Set<string>()
  svg.querySelectorAll<SVGGElement>('g.node[data-id]').forEach((n) => {
    // RAIL_<name> is the generation convention (exact); the text test covers
    // diagrams drawn before it.
    const id = n.getAttribute('data-id')!
    if (id.startsWith('RAIL_') || isRailNode(n.textContent ?? '')) railNodes.add(id)
  })
  const powerEdges = new Set<string>()
  svg.querySelectorAll<SVGGElement>('g.edgeLabel .label[data-id]').forEach((l) => {
    if (isPower(l.textContent ?? '')) powerEdges.add(l.getAttribute('data-id')!)
  })
  svg.querySelectorAll<SVGPathElement>('path[data-id]').forEach((p) => {
    const id = p.getAttribute('data-id')!
    const touchesRail = [...railNodes].some((r) => id.startsWith(`L_${r}_`) || id.includes(`_${r}_`))
    if (powerEdges.has(id) || touchesRail) {
      p.classList.add('power-flow')
      recolorMarkers(svg, p)
    }
  })
}

// Mermaid points every edge at one shared arrowhead <marker>, so a red power line
// would still end in a copper head. Give power edges a red copy of each marker.
function recolorMarkers(svg: SVGSVGElement, path: SVGPathElement) {
  for (const attr of ['marker-end', 'marker-start']) {
    const ref = /url\(#(.+)\)/.exec(path.getAttribute(attr) ?? '')?.[1]
    if (!ref) continue
    const powerId = `${ref}-power`
    if (!svg.getElementById(powerId)) {
      const src = svg.getElementById(ref)
      if (!src) continue
      const copy = src.cloneNode(true) as SVGMarkerElement
      copy.id = powerId
      copy.querySelectorAll<SVGElement>('*').forEach((el) => {
        el.style.fill = 'var(--power-rail)'
        el.style.stroke = 'var(--power-rail)'
      })
      src.parentNode?.insertBefore(copy, src.nextSibling)
    }
    path.setAttribute(attr, `url(#${powerId})`)
  }
}

// ---------- block tints ----------

// Each subgraph gets a faint tint by what the block does, so blocks separate
// from the canvas and like functions read alike. Keyword match on the title;
// first match wins (so "USB-C input" is power, "CAN interface" is comms).
// The labels are what the side panel's legend shows for each tint.
const NEUTRAL_TINT = '159, 176, 168'
const BLOCK_TINTS: [RegExp, string, string][] = [
  [/power|buck|boost|ldo|regulat|supply|input|rail|batter|protect|charg/i, '242, 96, 76', 'Power'], // red
  [/mcu|esp|micro|processor|controller|logic|fpga|cpu/i, '98, 142, 230', 'Compute'], // blue
  [/can\b|usb|uart|i2c|spi|ethernet|rs-?485|transceiver|comm|interface|bus|radio|wireless/i, '56, 178, 172', 'Comms'], // teal
  [/led|button|switch|user|control|indicator|display|buzzer/i, '224, 145, 47', 'User interface'], // amber
  [/header|connector|gpio|debug|program|boot|mechanical|mount/i, NEUTRAL_TINT, 'I/O & other'], // neutral, also the fallback
]
const tintFill = (rgb: string) => `rgba(${rgb}, 0.07)`
const tintStroke = (rgb: string) => `rgba(${rgb}, 0.45)`

function tintBlocks(svg: SVGSVGElement) {
  svg.querySelectorAll<SVGGElement>('g.cluster').forEach((g) => {
    const title = g.querySelector('.cluster-label')?.textContent ?? g.getAttribute('data-id') ?? ''
    const rgb = BLOCK_TINTS.find(([re]) => re.test(title))?.[1] ?? NEUTRAL_TINT
    const rect = g.querySelector<SVGRectElement>(':scope > rect')
    if (!rect) return
    rect.style.fill = tintFill(rgb)
    rect.style.stroke = tintStroke(rgb)
  })
}

// Key to the block tints: each swatch is drawn the way a block is on the canvas.
function TintLegend() {
  return (
    <div
      style={{
        marginTop: 'auto',
        paddingTop: 14,
        borderTop: `1px solid ${color.benchLine}`,
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: '8px 12px',
      }}
    >
      {BLOCK_TINTS.map(([, rgb, label]) => (
        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span
            style={{
              flexShrink: 0,
              width: 18,
              height: 12,
              borderRadius: 3,
              border: `1px solid ${tintStroke(rgb)}`,
              // the tint over the canvas colour, as it appears in the diagram
              background: `linear-gradient(${tintFill(rgb)}, ${tintFill(rgb)}), ${color.benchBase}`,
            }}
          />
          <span style={{ fontFamily: type.mono, fontSize: type.size.xs, color: color.inkSecondary }}>{label}</span>
        </div>
      ))}
    </div>
  )
}

// ---------- pan / zoom canvas ----------

interface View {
  x: number
  y: number
  k: number
}
const K_MIN = 0.05
const K_MAX = 8
const clampK = (k: number) => Math.min(K_MAX, Math.max(K_MIN, k))

// Engineering-paper grid in diagram space: it pans and zooms with the drawing.
// Minor lines every 20 units, major every 100; when zoomed out far enough that
// minor lines crowd (<10px apart), step up by 5x so density stays constant.
function gridStyle(v: View): React.CSSProperties {
  let minor = 20 * v.k
  while (minor < 10) minor *= 5
  const major = minor * 5
  const line = (a: number) => `rgba(159, 176, 168, ${a})`
  return {
    backgroundImage: [
      `linear-gradient(to right, ${line(0.09)} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${line(0.09)} 1px, transparent 1px)`,
      `linear-gradient(to right, ${line(0.04)} 1px, transparent 1px)`,
      `linear-gradient(to bottom, ${line(0.04)} 1px, transparent 1px)`,
    ].join(','),
    backgroundSize: `${major}px ${major}px, ${major}px ${major}px, ${minor}px ${minor}px, ${minor}px ${minor}px`,
    backgroundPosition: `${v.x}px ${v.y}px`,
  }
}

// The diagram at its natural size inside a transformed layer. Wheel zooms about
// the cursor, drag pans, double-click fits. Until the user moves it, the view
// refits whenever the pane resizes (tab shown, side panel toggled, window).
// A moved view is remembered per diagram (keyed by its source) while the app runs,
// so it comes back after switching tabs; a fresh launch opens fitted. A
// regenerated diagram is new source and fits fresh.
const FLOW_KEY = 'pcb.diagram.flow'
const readFlow = () => {
  try {
    return localStorage.getItem(FLOW_KEY) !== 'off'
  } catch {
    return true
  }
}

const VIEW_PREFIX = 'pcb.diagram.view:'
const viewKey = (code: string) => {
  let h = 0x811c9dc5 // FNV-1a
  for (let i = 0; i < code.length; i++) h = Math.imul(h ^ code.charCodeAt(i), 0x01000193)
  return `${VIEW_PREFIX}${(h >>> 0).toString(36)}`
}
// In memory only: gone when the app closes.
const savedViews = new Map<string, View>()

// Views used to persist across launches; drop any left over from that.
try {
  Object.keys(localStorage)
    .filter((k) => k.startsWith(VIEW_PREFIX))
    .forEach((k) => localStorage.removeItem(k))
} catch {
  /* nothing to clean */
}

function PanZoom({ code, onError }: { code: string; onError: (e: string) => void }) {
  const frame = useRef<HTMLDivElement>(null)
  const layer = useRef<HTMLDivElement>(null)
  const size = useRef({ w: 0, h: 0 })
  const touched = useRef(false)
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null)
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 })
  const [panning, setPanning] = useState(false)
  const [flow, setFlow] = useState(readFlow)
  const codeRef = useRef(code)
  codeRef.current = code

  const anim = useRef<number | null>(null)
  const viewRef = useRef(view)
  viewRef.current = view
  const stopAnim = useCallback(() => {
    if (anim.current !== null) cancelAnimationFrame(anim.current)
    anim.current = null
  }, [])

  // `animate` (double-click / fit button): glide there; any user input cancels it.
  // Interpolate what's under the pane centre (in diagram coordinates) and the zoom
  // (in log space), then derive the offset. Lerping the screen offset directly
  // swings wildly when zoomed in — a pixel there spans a sliver of diagram.
  // Automatic refits (first render, resize) snap.
  const fit = useCallback((animate = false) => {
    const f = frame.current
    const { w, h } = size.current
    if (!f || !w || !h || !f.clientWidth || !f.clientHeight) return
    const pad = 32
    const k = clampK(Math.min((f.clientWidth - pad * 2) / w, (f.clientHeight - pad * 2) / h))
    const to = { k, x: (f.clientWidth - w * k) / 2, y: (f.clientHeight - h * k) / 2 }
    stopAnim()
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (!animate || reduced) {
      setView(to)
    } else {
      const from = viewRef.current
      const cx = f.clientWidth / 2
      const cy = f.clientHeight / 2
      // diagram point at the pane centre, before and after
      const a = { x: (cx - from.x) / from.k, y: (cy - from.y) / from.k }
      const b = { x: (cx - to.x) / to.k, y: (cy - to.y) / to.k }
      const start = performance.now()
      const DUR = 320
      const step = (now: number) => {
        const p = Math.min(1, (now - start) / DUR)
        const e = 1 - Math.pow(1 - p, 3) // ease-out cubic
        const k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e)
        const px = a.x + (b.x - a.x) * e
        const py = a.y + (b.y - a.y) * e
        setView({ k, x: cx - px * k, y: cy - py * k })
        anim.current = p < 1 ? requestAnimationFrame(step) : null
      }
      anim.current = requestAnimationFrame(step)
    }
    touched.current = false
    savedViews.delete(viewKey(codeRef.current)) // fitted = nothing to restore
  }, [stopAnim])
  useEffect(() => stopAnim, [stopAnim])

  // Remember a moved view (debounced; the wheel fires fast).
  useEffect(() => {
    if (!touched.current) return
    const t = setTimeout(() => savedViews.set(viewKey(codeRef.current), view), 250)
    return () => clearTimeout(t)
  }, [view])

  // render → natural size → fit
  useEffect(() => {
    let alive = true
    renderSvg(code)
      .then((svg) => {
        if (!alive || !layer.current) return
        layer.current.innerHTML = svg
        const el = layer.current.querySelector('svg')
        if (!el) return
        const vb = el.viewBox.baseVal
        const w = vb?.width || el.getBoundingClientRect().width
        const h = vb?.height || el.getBoundingClientRect().height
        // Mermaid sizes the svg to its container; pin it to its own geometry.
        el.removeAttribute('style')
        el.setAttribute('width', String(w))
        el.setAttribute('height', String(h))
        el.style.display = 'block'
        markPowerEdges(el)
        tintBlocks(el)
        size.current = { w, h }
        const saved = savedViews.get(viewKey(code))
        if (saved) {
          touched.current = true
          setView(saved)
        } else fit()
      })
      .catch((e) => alive && onError(String(e)))
    return () => {
      alive = false
    }
  }, [code, fit, onError])

  useEffect(() => {
    const f = frame.current
    if (!f) return
    const ro = new ResizeObserver(() => !touched.current && fit())
    ro.observe(f)
    return () => ro.disconnect()
  }, [fit])

  // Wheel must be non-passive to stop the page scrolling.
  useEffect(() => {
    const f = frame.current
    if (!f) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      stopAnim()
      const r = f.getBoundingClientRect()
      const cx = e.clientX - r.left
      const cy = e.clientY - r.top
      touched.current = true
      setView((v) => {
        const k = clampK(v.k * Math.exp(-e.deltaY * 0.0015))
        return { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k }
      })
    }
    f.addEventListener('wheel', onWheel, { passive: false })
    return () => f.removeEventListener('wheel', onWheel)
  }, [stopAnim])

  // Button zoom steps about the pane centre.
  const zoomBy = (factor: number) => {
    const f = frame.current
    if (!f) return
    const cx = f.clientWidth / 2
    const cy = f.clientHeight / 2
    stopAnim()
    touched.current = true
    setView((v) => {
      const k = clampK(v.k * factor)
      return { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k }
    })
  }

  return (
    <div
      ref={frame}
      className={flow ? 'flow-on' : undefined}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        ;(e.target as Element).setPointerCapture?.(e.pointerId)
        stopAnim()
        drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y }
        setPanning(true)
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (!d) return
        touched.current = true
        setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }))
      }}
      onPointerUp={() => {
        drag.current = null
        setPanning(false)
      }}
      onDoubleClick={() => fit(true)}
      style={{
        ...gridStyle(view),
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        cursor: panning ? 'grabbing' : 'grab',
        touchAction: 'none',
        userSelect: 'none',
      }}
    >
      <div
        ref={layer}
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          transformOrigin: '0 0',
          transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
        }}
      />
      {/* zoom controls — bottom right, over the canvas */}
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
        <IconButton label="Zoom out" onClick={() => zoomBy(1 / 1.25)}>
          <Minus size={15} strokeWidth={1.75} />
        </IconButton>
        <span
          style={{
            fontFamily: type.mono,
            fontSize: type.size.xs,
            color: color.inkSecondary,
            minWidth: 44,
            textAlign: 'center',
          }}
        >
          {Math.round(view.k * 100)}%
        </span>
        <IconButton label="Zoom in" onClick={() => zoomBy(1.25)}>
          <Plus size={15} strokeWidth={1.75} />
        </IconButton>
        <span style={{ width: 1, height: 16, background: color.benchLine, margin: '0 3px' }} />
        <IconButton
          label={flow ? 'Stop power-flow animation' : 'Animate power flow'}
          active={flow}
          onClick={() =>
            setFlow((on) => {
              try {
                localStorage.setItem(FLOW_KEY, on ? 'off' : 'on')
              } catch {
                /* per-viewer nicety only */
              }
              return !on
            })
          }
        >
          <Zap size={14} strokeWidth={1.75} />
        </IconButton>
        <IconButton label="Fit to view (double-click)" onClick={() => fit(true)}>
          <Maximize size={14} strokeWidth={1.75} />
        </IconButton>
      </div>
    </div>
  )
}

export function IconButton({
  label,
  onClick,
  active,
  children,
}: {
  label: string
  onClick: () => void
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 5,
        border: 0,
        background: 'transparent',
        color: active ? color.copperBright : color.inkSecondary,
      }}
    >
      {children}
    </button>
  )
}

// ---------- side panel ----------

function PanelSection({ label, tone, children }: { label: string; tone?: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 20 }}>
      <div
        style={{
          fontFamily: type.mono,
          fontSize: type.size.sm,
          fontWeight: type.weight.semi,
          color: tone ?? color.inkPrimary,
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          marginBottom: 10,
        }}
      >
        {label}
      </div>
      <div style={{ color: color.inkSecondary, lineHeight: type.line.base, fontSize: type.size.base }}>{children}</div>
    </section>
  )
}

const PANEL_KEY = 'pcb.diagram.panel'
const readPanel = () => {
  try {
    return localStorage.getItem(PANEL_KEY) !== 'closed'
  } catch {
    return true
  }
}

// The Diagram tab: the whole pane is a pan/zoom canvas; purpose + last change
// live in a collapsible panel on the right.
export function DiagramTab({
  model,
  loaded,
  refreshKey,
}: {
  model: string
  loaded: boolean
  refreshKey: number
}) {
  const [data, setData] = useState<DiagramResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [renderErr, setRenderErr] = useState('')
  const [panel, setPanel] = useState(readPanel)

  // Load the last saved diagram so it survives an app restart (no re-spend);
  // refetched on `refreshKey` so an agent redraw from chat shows up here.
  useEffect(() => {
    if (!loaded) return
    getLastDiagram()
      .then((d) => {
        if (d.mermaid) setData(d)
      })
      .catch(() => {})
  }, [loaded, refreshKey])

  useEffect(() => setRenderErr(''), [data?.mermaid])
  const onRenderError = useCallback((e: string) => setRenderErr(e), [])

  function togglePanel() {
    setPanel((open) => {
      try {
        localStorage.setItem(PANEL_KEY, open ? 'closed' : 'open')
      } catch {
        /* per-viewer nicety only */
      }
      return !open
    })
  }

  async function gen() {
    setErr('')
    setBusy(true)
    try {
      setData(await diagram(model))
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const hasText = !!(data?.purpose || data?.note)

  return (
    <div style={{ display: 'flex', flex: 1, minWidth: 0, height: '100%' }}>
      <div style={{ position: 'relative', flex: 1, minWidth: 0, background: color.benchBase }}>
        {data?.mermaid && !renderErr && <PanZoom code={data.mermaid} onError={onRenderError} />}

        {/* empty / error states, centred in the canvas */}
        {(!data?.mermaid || renderErr) && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 24,
              color: color.inkMuted,
              textAlign: 'center',
            }}
          >
            {renderErr ? (
              <div style={{ maxWidth: 720, textAlign: 'left' }}>
                <pre style={{ color: color.statusCritical, whiteSpace: 'pre-wrap' }}>{renderErr}</pre>
                <pre
                  style={{
                    background: color.benchVoid,
                    border: `1px solid ${color.benchLine}`,
                    padding: 8,
                    overflow: 'auto',
                    maxHeight: 360,
                    borderRadius: radius.sm,
                    color: color.inkSecondary,
                  }}
                >
                  {data?.mermaid}
                </pre>
              </div>
            ) : !loaded ? (
              'Open a project first.'
            ) : (
              'No diagram yet — generate one, or ask the copilot once the parts are in.'
            )}
          </div>
        )}

        {/* actions — top left, over the canvas */}
        <div style={{ position: 'absolute', left: 14, top: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            onClick={gen}
            disabled={!loaded || busy || !model}
            title={model ? undefined : 'Connect a model first (gear icon, top right)'}
            style={{ background: color.benchRaised }}
          >
            {busy ? 'Generating…' : data?.mermaid ? 'Regenerate diagram' : 'Generate diagram'}
            <span style={{ color: color.statusCaution, marginLeft: 6, fontSize: type.size.xs }}>LLM · paid</span>
          </button>
          {/* one-step undo: swaps with the diagram the last regenerate replaced (free) */}
          {data?.has_previous && (
            <button
              onClick={() => revertDiagram().then(setData).catch((e) => setErr(String(e)))}
              disabled={busy}
              style={{ background: color.benchRaised }}
              title="swap back to the diagram the last regenerate replaced"
            >
              Restore previous
            </button>
          )}
        </div>
        {err && (
          <pre
            style={{
              position: 'absolute',
              left: 14,
              top: 54,
              right: 14,
              margin: 0,
              color: color.statusCritical,
              whiteSpace: 'pre-wrap',
            }}
          >
            {err}
          </pre>
        )}

        {/* panel toggle — top right */}
        {hasText && (
          <div style={{ position: 'absolute', right: 14, top: 14 }}>
            <button
              onClick={togglePanel}
              aria-label={panel ? 'Hide details' : 'Show details'}
              title={panel ? 'Hide details' : 'Show details'}
              style={{ display: 'inline-flex', alignItems: 'center', padding: 6, background: color.benchRaised }}
            >
              {panel ? (
                <PanelRightClose size={15} strokeWidth={1.75} />
              ) : (
                <PanelRightOpen size={15} strokeWidth={1.75} />
              )}
            </button>
          </div>
        )}
      </div>

      {hasText && panel && (
        <aside
          style={{
            width: 340,
            flexShrink: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'auto',
            padding: '18px 20px',
            borderLeft: `1px solid ${color.benchLine}`,
            background: color.benchRaised,
          }}
        >
          {data?.purpose && (
            <PanelSection label="Purpose" tone={color.copper}>
              {/* Markdown: overview line + numbered power path + bulleted signals */}
              <div className="md caption">
                <ReactMarkdown>{data.purpose}</ReactMarkdown>
              </div>
            </PanelSection>
          )}
          {data?.note && (
            <PanelSection label="Last change" tone={color.agent}>
              {data.note}
            </PanelSection>
          )}
          {/* pinned to the panel's foot; follows the text when that overflows */}
          <TintLegend />
        </aside>
      )}
    </div>
  )
}
