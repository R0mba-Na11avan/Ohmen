import { useEffect, useRef } from 'react'
import { color } from '../theme'

// Ambient backdrop for the empty start screen: a fixed network of faded copper
// pipes (pipes-screensaver style) with glowing orbs riding them. The pipes are
// drawn once per resize; orbs animate on a second canvas, each leaving a glow
// trail redrawn from its recent position history (fades cleanly, no residue).

const CELL = 36 // grid pitch, px
const FILL = 1 // fraction of grid cells pipes may occupy
const TRAIL_MS = 2200 // how long a trail glows behind an orb
const MAX_ORBS = 14

type Pt = { x: number; y: number }
type Pipe = Pt[] // cell centers, consecutive points one cell apart
type Orb = { pipe: number; s: number; dir: 1 | -1; speed: number; trail: (Pt & { t: number })[]; dying: boolean }

const DIRS: Pt[] = [{ x: 1, y: 0 }, { x: 0, y: 1 }, { x: -1, y: 0 }, { x: 0, y: -1 }]

function buildPipes(cols: number, rows: number): Pipe[] {
  const used = new Set<string>()
  const key = (c: number, r: number) => `${c},${r}`
  const free = (c: number, r: number) => c >= 0 && r >= 0 && c < cols && r < rows && !used.has(key(c, r))
  const pipes: Pipe[] = []
  const target = cols * rows * FILL
  for (let tries = 0; used.size < target && tries < 400; tries++) {
    let c = Math.floor(Math.random() * cols)
    let r = Math.floor(Math.random() * rows)
    if (!free(c, r)) continue
    let d = Math.floor(Math.random() * 4)
    const cells: Pt[] = [{ x: c, y: r }]
    used.add(key(c, r))
    const len = 12 + Math.floor(Math.random() * 40)
    for (let i = 0; i < len; i++) {
      // Mostly straight; sometimes turn. Try the preferred heading, then the turns.
      const turn = Math.random() < 0.22 ? (Math.random() < 0.5 ? 1 : 3) : 0
      const order = [(d + turn) % 4, (d + 1) % 4, (d + 3) % 4].filter((v, j, a) => a.indexOf(v) === j)
      const next = order.find((nd) => free(c + DIRS[nd].x, r + DIRS[nd].y))
      if (next === undefined) break
      d = next
      c += DIRS[d].x
      r += DIRS[d].y
      used.add(key(c, r))
      cells.push({ x: c, y: r })
    }
    if (cells.length >= 4) pipes.push(cells)
    else cells.forEach((p) => used.delete(key(p.x, p.y)))
  }
  return pipes
}

function drawPipes(ctx: CanvasRenderingContext2D, pipes: Pipe[], toPx: (p: Pt) => Pt) {
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const pipe of pipes) {
    const pts = pipe.map(toPx)
    const path = () => {
      ctx.beginPath()
      pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)))
    }
    // body, then a thin offset highlight so the pipe reads as a tube
    path()
    ctx.strokeStyle = color.copperDim
    ctx.globalAlpha = 0.55
    ctx.lineWidth = 7
    ctx.stroke()
    ctx.save()
    ctx.translate(-1.2, -1.2)
    path()
    ctx.strokeStyle = color.copper
    ctx.globalAlpha = 0.28
    ctx.lineWidth = 2
    ctx.stroke()
    ctx.restore()
    // elbow joints and end caps, like the screensaver's ball joints
    ctx.fillStyle = color.copperDim
    ctx.globalAlpha = 0.7
    pts.forEach((p, i) => {
      const a = pts[i - 1]
      const b = pts[i + 1]
      const elbow = a && b && (a.x - p.x) * (b.y - p.y) !== (a.y - p.y) * (b.x - p.x)
      if (!a || !b || elbow) {
        ctx.beginPath()
        ctx.arc(p.x, p.y, 5.5, 0, Math.PI * 2)
        ctx.fill()
      }
    })
  }
  ctx.globalAlpha = 1
}

export function PipesBackdrop() {
  const wrapRef = useRef<HTMLDivElement>(null)
  const pipesRef = useRef<HTMLCanvasElement>(null)
  const fxRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const wrap = wrapRef.current
    const pc = pipesRef.current
    const fc = fxRef.current
    if (!wrap || !pc || !fc) return
    const pctx = pc.getContext('2d')
    const fctx = fc.getContext('2d')
    if (!pctx || !fctx) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    let pipes: Pipe[] = []
    let orbs: Orb[] = []
    let ox = 0
    let oy = 0
    const toPx = (p: Pt): Pt => ({ x: ox + p.x * CELL, y: oy + p.y * CELL })

    const spawn = (): Orb => {
      const pipe = Math.floor(Math.random() * pipes.length)
      const n = pipes[pipe].length - 1
      return {
        pipe,
        s: Math.random() * n,
        dir: Math.random() < 0.5 ? 1 : -1,
        speed: 1.4 + Math.random() * 2.6, // cells per second
        trail: [],
        dying: false,
      }
    }

    const at = (o: Orb): Pt => {
      const cells = pipes[o.pipe]
      const i = Math.min(Math.floor(o.s), cells.length - 2)
      const f = o.s - i
      const a = toPx(cells[i])
      const b = toPx(cells[i + 1])
      return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f }
    }

    const layout = () => {
      const { width: w, height: h } = wrap.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      for (const [cv, cx] of [[pc, pctx], [fc, fctx]] as const) {
        cv.width = Math.round(w * dpr)
        cv.height = Math.round(h * dpr)
        cx.setTransform(dpr, 0, 0, dpr, 0, 0)
      }
      const cols = Math.ceil(w / CELL) + 1
      const rows = Math.ceil(h / CELL) + 1
      ox = (w - (cols - 1) * CELL) / 2
      oy = (h - (rows - 1) * CELL) / 2
      pipes = buildPipes(cols, rows)
      pctx.clearRect(0, 0, w, h)
      drawPipes(pctx, pipes, toPx)
      const count = Math.min(MAX_ORBS, Math.max(3, Math.round(pipes.length / 2)))
      orbs = pipes.length ? Array.from({ length: count }, spawn) : []
    }

    let raf = 0
    let last = performance.now()
    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000) // clamp after a hidden tab
      last = now
      const { width: w, height: h } = wrap.getBoundingClientRect()
      fctx.clearRect(0, 0, w, h)
      fctx.globalCompositeOperation = 'lighter'
      fctx.lineCap = 'round'

      orbs = orbs.map((o) => {
        if (!o.dying) {
          o.s += o.dir * o.speed * dt
          const end = pipes[o.pipe].length - 1
          if (o.s <= 0 || o.s >= end) {
            o.s = Math.max(0, Math.min(end, o.s))
            o.dying = true // reached a pipe end: let the trail fade out, then respawn
          }
          o.trail.push({ ...at(o), t: now })
        }
        o.trail = o.trail.filter((p) => now - p.t < TRAIL_MS)
        return o.dying && o.trail.length === 0 ? spawn() : o
      })

      for (const o of orbs) {
        const tr = o.trail
        for (let i = 1; i < tr.length; i++) {
          const k = 1 - (now - tr[i].t) / TRAIL_MS // 1 at the head, 0 at the tail
          fctx.beginPath()
          fctx.moveTo(tr[i - 1].x, tr[i - 1].y)
          fctx.lineTo(tr[i].x, tr[i].y)
          fctx.strokeStyle = `rgba(238,167,102,${0.10 * k})`
          fctx.lineWidth = 12
          fctx.stroke()
          fctx.strokeStyle = `rgba(238,167,102,${0.55 * k * k})`
          fctx.lineWidth = 3
          fctx.stroke()
        }
        if (!o.dying && tr.length) {
          const p = tr[tr.length - 1]
          const g = fctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 16)
          g.addColorStop(0, 'rgba(255,214,170,0.95)')
          g.addColorStop(0.25, 'rgba(238,167,102,0.55)')
          g.addColorStop(1, 'rgba(238,167,102,0)')
          fctx.fillStyle = g
          fctx.beginPath()
          fctx.arc(p.x, p.y, 16, 0, Math.PI * 2)
          fctx.fill()
        }
      }
      fctx.globalCompositeOperation = 'source-over'
      raf = requestAnimationFrame(frame)
    }

    layout()
    if (!still) raf = requestAnimationFrame(frame)
    let resizeT = 0
    const ro = new ResizeObserver(() => {
      clearTimeout(resizeT)
      resizeT = window.setTimeout(layout, 150)
    })
    ro.observe(wrap)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(resizeT)
      ro.disconnect()
    }
  }, [])

  const canvas: React.CSSProperties = { position: 'absolute', inset: 0, width: '100%', height: '100%' }
  return (
    <div
      ref={wrapRef}
      aria-hidden
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        opacity: 0.45,
        // fade toward the center so the start-screen text stays clear
        maskImage: 'radial-gradient(ellipse 55% 50% at 50% 50%, transparent 15%, black 85%)',
        WebkitMaskImage: 'radial-gradient(ellipse 55% 50% at 50% 50%, transparent 15%, black 85%)',
      }}
    >
      <canvas ref={pipesRef} style={canvas} />
      <canvas ref={fxRef} style={canvas} />
    </div>
  )
}
