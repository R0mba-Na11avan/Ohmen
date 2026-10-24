import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight, Plus, SendHorizontal, Square, X } from 'lucide-react'
import {
  CHAT_IMAGE_MAX_BYTES,
  CHAT_IMAGE_TYPES,
  deleteChat,
  getChat,
  listChats,
  streamChat,
  uploadChatImage,
  type ChatErrorInfo,
  type ChatImage,
  type ChatSessionSummary,
  type FindingCounts,
  type RateLimitInfo,
  type ReviewerRow,
  type TurnUsage,
} from '../api'
import { getEffort, setEffort, type Effort } from '../effort'
import { pickGreeting } from '../greeting'
import type { ModelState } from '../hooks/useModel'
import { useReducedMotion } from '../hooks/useReducedMotion'
import { useSteadyText } from '../hooks/useSteadyText'
import { color, radius, type } from '../theme'
import { relTime } from '../time'
import { MermaidView } from './DiagramTab'
import { FootprintCard } from './FootprintCard'
import { EffortPicker } from './EffortPicker'
import { CopyButton, Markdown } from './Markdown'
import { LogoMark } from './marks'
import { ModelPicker } from './ModelPicker'
import { ReviewTeam, applyProgress, compact, usageLine } from './ReviewTeam'

interface ToolEvent {
  kind: 'call' | 'result'
  name: string
  info: string
}
interface Turn {
  q: string
  a: string
  thinking?: string
  tools: ToolEvent[]
  team?: ReviewerRow[] // reviewers of a design review, live then persisted
  images?: ChatImage[]
  error?: string | null
  errorInfo?: ChatErrorInfo // e.g. a subscription's usage limit, with when it resets
  review?: FindingCounts | 'ran' // live counts, or 'ran' when reloaded from history
  diagram?: { note: string; mermaid?: string } // mermaid only live; history keeps the note
  footprints?: string[] // generated footprint proposals awaiting (or past) the engineer's call
  notices?: string[] // e.g. "continuing on your API key"
  stopped?: boolean // the user stopped the reply part-way
  rateLimit?: RateLimitInfo // a subscription's usage window after this turn (live only)
  usage?: TurnUsage | null // what this turn cost in tokens
}

// An image picked in the composer, not yet sent: previewed from a blob URL.
interface Attachment {
  key: string
  file: File
  url: string
}

const REVIEW_TOOLS = new Set(['run_check', 'run_review', 'run_erc', 'run_drc'])
const DIAGRAM_PREFIX = 'Diagram updated: '

// A reloaded turn's footprint cards come from its create_footprint tool results.
function footprintsFromTools(tools: ToolEvent[]): string[] | undefined {
  const ids = tools.flatMap((x) =>
    x.kind === 'result' && x.name === 'create_footprint' ? [x.info.match(/^Footprint proposal \[(\w+)\]/)?.[1]] : [],
  )
  const found = ids.filter((id): id is string => !!id)
  return found.length ? found : undefined
}

// A reloaded turn's diagram card comes from its update_diagram tool result.
function diagramFromTools(tools: ToolEvent[]): Turn['diagram'] {
  const r = tools.find((x) => x.kind === 'result' && x.name === 'update_diagram' && x.info.startsWith(DIAGRAM_PREFIX))
  return r ? { note: r.info.slice(DIAGRAM_PREFIX.length).split('\n')[0] } : undefined
}

// The model's reasoning, collapsed to one line by default; click to read it,
// faded so it never competes with the answer.
function ThinkingBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginTop: 8 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={live ? 'breathe' : undefined}
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
        {live ? 'Thinking…' : 'Thought process'}
      </button>
      {open && (
        <div
          style={{
            marginTop: 4,
            marginLeft: 6,
            paddingLeft: 10,
            borderLeft: `1px solid ${color.benchLine}`,
            color: color.inkMuted,
            fontSize: type.size.sm,
            lineHeight: type.line.base,
            whiteSpace: 'pre-wrap',
            maxHeight: 260,
            overflow: 'auto',
            opacity: 0.8,
          }}
        >
          {text}
        </div>
      )}
    </div>
  )
}

// Attached-image thumbnail. Hover shows the file name; in the composer it also
// shows a copper ✕ (top right) that drops the image.
function Thumb({
  name,
  url,
  size,
  onOpen,
  onRemove,
}: {
  name: string
  url: string
  size?: number
  onOpen: () => void
  onRemove?: () => void
}) {
  return (
    <div className="attach-thumb" title={name} style={size ? { width: size, height: size } : undefined}>
      <button className="attach-open" onClick={onOpen} aria-label={`Preview ${name}`}>
        <img src={url} alt={name} />
      </button>
      <div className="attach-name">{name}</div>
      {onRemove && (
        <button className="attach-remove" onClick={onRemove} aria-label={`Remove ${name}`}>
          <X size={12} strokeWidth={2.25} />
        </button>
      )}
    </div>
  )
}

// Large preview of an attached image. Clicking anywhere off the image (or Esc)
// closes it; clicks on the image itself don't.
function Lightbox({ name, url, onClose }: { name: string; url: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <figure className="lightbox" role="dialog" aria-modal="true" aria-label={name} onClick={onClose} style={{ margin: 0 }}>
      <img src={url} alt={name} onClick={(e) => e.stopPropagation()} />
      <figcaption onClick={(e) => e.stopPropagation()}>{name}</figcaption>
    </figure>,
    document.body,
  )
}

// The agent's voice: unboxed prose on a purple rail (the old bubble's edge). While
// the reply streams it is let out at a steady typing pace and ends in a blinking
// purple caret; before the first word, the caret alone is the wait cue (it claims
// nothing about thinking). A finished reply gets a copy action (the full text).
function AgentReply({ text, live }: { text: string; live: boolean }) {
  const { text: shown, typing } = useSteadyText(text, live)
  return (
    <div className={live ? 'agent-turn live' : 'agent-turn'} style={{ marginTop: 10, minWidth: 0 }}>
      <div className={live || typing ? 'md streaming' : 'md'}>
        {shown ? (
          <Markdown text={shown} streaming={live || typing} />
        ) : live ? (
          <span className="caret solo" role="status" aria-label="Waiting for the reply" />
        ) : (
          <p>…</p>
        )}
      </div>
      {text && !live && !typing && (
        <div className="agent-actions">
          <CopyButton text={text} />
        </div>
      )}
    </div>
  )
}

// Reading column: the thread and composer share it, centered in whatever width
// the window gives (the leftover splits evenly instead of piling up on the right).
const COLUMN = { width: '100%', maxWidth: 780, margin: '0 auto' } as const
const COMPOSER_MAX = 168 // px, ~8 lines

// Session sidebar: drag its edge to resize (double-click resets). Remembered per viewer.
const SIDEBAR = { min: 180, max: 480, initial: 280 }
const SIDEBAR_KEY = 'pcb.chat.sidebar'
const clampSidebar = (w: number) => Math.round(Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, w)))
const readSidebar = () => {
  try {
    const w = Number(localStorage.getItem(SIDEBAR_KEY))
    return w ? clampSidebar(w) : SIDEBAR.initial
  } catch {
    return SIDEBAR.initial
  }
}
const saveSidebar = (w: number) => {
  try {
    localStorage.setItem(SIDEBAR_KEY, String(w))
  } catch {
    /* per-viewer nicety only */
  }
}

// The resize grip between sidebar and thread: a hairline that turns copper on
// hover/drag, with a wider invisible hit area. Arrow keys nudge it too.
function ResizeHandle({ width, onChange }: { width: number; onChange: (w: number, done: boolean) => void }) {
  const drag = useRef<{ x: number; w: number } | null>(null)
  const [hot, setHot] = useState(false)
  const [dragging, setDragging] = useState(false)
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize chat list"
      aria-valuemin={SIDEBAR.min}
      aria-valuemax={SIDEBAR.max}
      aria-valuenow={width}
      tabIndex={0}
      onPointerEnter={() => setHot(true)}
      onPointerLeave={() => setHot(false)}
      onFocus={() => setHot(true)}
      onBlur={() => setHot(false)}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId)
        drag.current = { x: e.clientX, w: width }
        setDragging(true)
      }}
      onPointerMove={(e) => {
        if (drag.current) onChange(clampSidebar(drag.current.w + e.clientX - drag.current.x), false)
      }}
      onPointerUp={(e) => {
        if (drag.current) onChange(clampSidebar(drag.current.w + e.clientX - drag.current.x), true)
        drag.current = null
        setDragging(false)
      }}
      onDoubleClick={() => onChange(SIDEBAR.initial, true)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 10
        if (e.key === 'ArrowLeft') onChange(clampSidebar(width - step), true)
        else if (e.key === 'ArrowRight') onChange(clampSidebar(width + step), true)
      }}
      style={{
        width: 9,
        flexShrink: 0,
        marginRight: 12,
        cursor: 'col-resize',
        display: 'flex',
        justifyContent: 'center',
        touchAction: 'none',
        outline: 'none',
      }}
    >
      <div
        style={{
          width: 1,
          transform: hot || dragging ? 'scaleX(2)' : 'none',
          background: hot || dragging ? color.copper : color.benchLine,
          transition: 'background 120ms var(--ease-out), transform 120ms var(--ease-out)',
        }}
      />
    </div>
  )
}

// Agentic chat with per-project sessions (Claude-Desktop style). The session list
// is the conversations; the BOM/memory it edits stay project-global.
function resetTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

// The subscription's usage window is spent: say when it resets, offer the API key.
function UsageLimitCard({
  resetsAt,
  canSwitch,
  onSwitch,
  onManage,
}: {
  resetsAt: number | null
  canSwitch: boolean
  onSwitch: () => void
  onManage: () => void
}) {
  return (
    <div
      style={{
        marginTop: 8,
        padding: '10px 12px',
        border: `1px solid ${color.statusCaution}`,
        background: color.statusCautionFaint,
        borderRadius: radius.md,
        display: 'grid',
        gap: 8,
        fontSize: type.size.sm,
        lineHeight: type.line.sm,
      }}
    >
      <span style={{ color: color.inkPrimary }}>
        Your subscription’s usage limit is reached
        {resetsAt ? ` — it resets at ${resetTime(resetsAt)}` : ''}. Anything already found is kept.
      </span>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {canSwitch ? (
          <button onClick={onSwitch} style={{ borderColor: color.copper, color: color.copperBright }}>
            Switch to API key
          </button>
        ) : (
          <button onClick={onManage}>Add an API key…</button>
        )}
      </div>
    </div>
  )
}

export function ChatPane({
  models,
  projectName,
  onManageModels,
  onBomChanged,
  onOpenFindings,
  onReviewRan,
  onOpenDiagram,
  onDiagramChanged,
  onRoutesChanged,
}: {
  models: ModelState // the app-wide pick; its picker sits under the composer
  projectName: string | null
  onManageModels: () => void
  onBomChanged: () => void
  onOpenFindings: () => void
  onReviewRan: () => void
  onOpenDiagram: () => void
  onDiagramChanged: () => void
  onRoutesChanged: () => void // the preferred route changed: re-list models
}) {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [sidebarW, setSidebarW] = useState(readSidebar)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [attachError, setAttachError] = useState<string | null>(null)
  const [dropping, setDropping] = useState(false)
  const [preview, setPreview] = useState<{ name: string; url: string } | null>(null)
  const closePreview = useCallback(() => setPreview(null), [])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const { model, info: modelInfo } = models
  const [effort, setEffortState] = useState<Effort>(getEffort)
  const reducedMotion = useReducedMotion()

  // A new chat (nothing picked, nothing said) shows the greeting with the composer
  // centred under it; once there is a conversation the composer docks at the
  // bottom. `ready` waits for the first session list, so reopening a project with
  // chats doesn't flash the greeting before the latest chat is picked.
  const [ready, setReady] = useState(false)
  const [newChat, setNewChat] = useState(0)
  const empty = ready && activeId === null && turns.length === 0
  const greeting = useMemo(() => pickGreeting(new Date(), projectName), [newChat, projectName])
  const mainRef = useRef<HTMLDivElement>(null)
  const dockRef = useRef<HTMLDivElement>(null)
  const flipFrom = useRef<number | null>(null)
  const [exitGreeting, setExitGreeting] = useState<{ text: string; style: CSSProperties } | null>(null)

  // What the picked model can't do, said up front instead of failing on send.
  const sendBlock = !modelInfo
    ? 'No model connected yet.'
    : !modelInfo.supports_tools
      ? `${modelInfo.model} can't call tools, which the copilot needs. Pick another model.`
      : ''
  const attachBlock =
    modelInfo && !modelInfo.supports_vision ? `${modelInfo.model} can't read images.` : ''

  // Images arrive by the + button, paste, or drop. Anything the models can't
  // take (wrong type, too big) is refused here, by name, before it is sent.
  function addFiles(files: Iterable<File>) {
    if (attachBlock) {
      setAttachError(`${attachBlock} Pick a vision model to attach images.`)
      return
    }
    const ok: Attachment[] = []
    const refused: string[] = []
    for (const file of files) {
      if (!CHAT_IMAGE_TYPES.includes(file.type)) refused.push(`${file.name} isn't a PNG, JPEG, GIF or WebP`)
      else if (file.size > CHAT_IMAGE_MAX_BYTES) refused.push(`${file.name} is over 5 MB`)
      else ok.push({ key: crypto.randomUUID(), file, url: URL.createObjectURL(file) })
    }
    setAttachError(refused.length ? refused.join('; ') : null)
    if (ok.length) setAttachments((a) => [...a, ...ok])
  }

  function removeAttachment(key: string) {
    setAttachments((a) => {
      a.filter((x) => x.key === key).forEach((x) => URL.revokeObjectURL(x.url))
      return a.filter((x) => x.key !== key)
    })
  }

  // Composer grows with its wrapped text up to ~8 lines, then scrolls. Sizing is
  // border-box and scrollHeight excludes the border, so add it back — otherwise the
  // box is 2px short, always overflows, and wheel-scrolls (the text wobbles).
  // Hidden (another tab), everything measures 0 — skip, and re-measure when the
  // box gets a width again, or it would collapse to its padding.
  const fitComposer = useCallback(() => {
    const el = inputRef.current
    if (!el || el.offsetParent === null) return
    el.style.height = 'auto'
    const border = el.offsetHeight - el.clientHeight
    const want = el.scrollHeight + border
    el.style.height = `${Math.min(want, COMPOSER_MAX)}px`
    el.style.overflowY = want > COMPOSER_MAX ? 'auto' : 'hidden'
  }, [])
  useLayoutEffect(fitComposer, [msg, fitComposer])
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    let lastWidth = -1
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width
      if (w !== lastWidth) {
        lastWidth = w
        fitComposer()
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [fitComposer])

  async function loadSessions(select?: string) {
    try {
      const s = await listChats()
      setSessions(s)
      if (select) selectSession(select)
      else if (!ready && activeId === null && s.length && turns.length === 0) selectSession(s[0].id)
    } catch {
      /* no project yet */
    } finally {
      setReady(true)
    }
  }

  async function selectSession(id: string) {
    setActiveId(id)
    try {
      const d = await getChat(id)
      setTurns(
        d.transcript.map((t) => ({
          q: t.q,
          a: t.a,
          thinking: t.thinking,
          tools: t.tools ?? [],
          team: t.team?.length ? t.team : undefined,
          images: t.images,
          error: t.error,
          stopped: t.stopped,
          usage: t.usage,
          review: (t.tools ?? []).some((x) => x.kind === 'result' && REVIEW_TOOLS.has(x.name))
            ? 'ran'
            : undefined,
          diagram: diagramFromTools(t.tools ?? []),
          footprints: footprintsFromTools(t.tools ?? []),
        })),
      )
    } catch {
      setTurns([])
    }
  }

  function startNew() {
    setActiveId(null)
    setTurns([])
    setMsg('')
    attachments.forEach((a) => URL.revokeObjectURL(a.url))
    setAttachments([])
    setAttachError(null)
    setNewChat((n) => n + 1)
  }

  async function removeSession(id: string) {
    await deleteChat(id)
    if (id === activeId) startNew()
    loadSessions()
  }

  useEffect(() => {
    loadSessions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // First send from a new chat: the composer glides from the centre to its docked
  // spot (FLIP: measure here, play it back once the layout has switched) while
  // the greeting fades where it stood.
  useLayoutEffect(() => {
    const from = flipFrom.current
    flipFrom.current = null
    const el = dockRef.current
    if (from == null || !el || empty) return
    const dy = from - el.getBoundingClientRect().top
    if (Math.abs(dy) > 1) {
      el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }], {
        duration: 220,
        easing: 'cubic-bezier(0.2, 0, 0, 1)',
      })
    }
  }, [empty])

  useEffect(() => {
    if (empty) inputRef.current?.focus()
  }, [empty])

  // The turn currently streaming.
  const live = (i: number) => busy && i === turns.length - 1

  const patchLast = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)))

  // Stop the reply in flight: the sidecar cancels the turn (and kills a
  // subscription CLI) when its socket closes. What already arrived stays.
  const stopRef = useRef<AbortController | null>(null)
  function stop() {
    if (!busy || !stopRef.current) return
    stopRef.current.abort()
    stopRef.current = null
    patchLast((t) => ({ ...t, stopped: true }))
    setBusy(false)
    loadSessions()
  }

  // The thread follows a growing reply while you're at the bottom; scroll up to
  // read and it stays put until you come back down (or send).
  const threadRef = useRef<HTMLDivElement>(null)
  const columnRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useEffect(() => {
    const el = threadRef.current
    const col = columnRef.current
    if (!el || !col) return
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    }
    const ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight
    })
    el.addEventListener('scroll', onScroll, { passive: true })
    ro.observe(col)
    return () => {
      el.removeEventListener('scroll', onScroll)
      ro.disconnect()
    }
  }, [])

  async function send() {
    const q = msg.trim()
    const picked = attachments
    if ((!q && picked.length === 0) || busy || sendBlock) return
    if (empty && !reducedMotion && dockRef.current && mainRef.current) {
      flipFrom.current = dockRef.current.getBoundingClientRect().top
      const g = mainRef.current.querySelector('.greeting')?.getBoundingClientRect()
      const m = mainRef.current.getBoundingClientRect()
      if (g) setExitGreeting({ text: greeting, style: { top: g.top - m.top, left: g.left - m.left, width: g.width } })
    }
    setMsg('')
    setAttachments([])
    setAttachError(null)
    setBusy(true)
    stick.current = true
    const ctl = new AbortController()
    stopRef.current = ctl
    // Show the turn at once from the blob previews, then swap in the stored copies.
    const previews = picked.map((p) => ({ id: p.key, name: p.file.name, url: p.url }))
    setTurns((ts) => [...ts, { q, a: '', tools: [], images: previews }])
    let images: ChatImage[] = []
    if (picked.length) {
      try {
        images = await Promise.all(picked.map((p) => uploadChatImage(p.file)))
      } catch (e) {
        // Nothing was sent: hand the text and images back to the composer.
        setTurns((ts) => ts.slice(0, -1))
        setMsg(q)
        setAttachments(picked)
        setAttachError(`Couldn't attach: ${e instanceof Error ? e.message : String(e)}`)
        setBusy(false)
        return
      }
      if (ctl.signal.aborted) return // stopped while the images uploaded
      patchLast((t) => ({ ...t, images }))
      picked.forEach((p) => URL.revokeObjectURL(p.url))
    }
    await streamChat(q, images, model, activeId, {
      onSession: (id) => setActiveId(id),
      onThinking: (text) => patchLast((t) => ({ ...t, thinking: (t.thinking ?? '') + text })),
      onToken: (text) => patchLast((t) => ({ ...t, a: t.a + text })),
      onTool: (kind, name, info) => patchLast((t) => ({ ...t, tools: [...t.tools, { kind, name, info }] })),
      onReviewProgress: (ev) => patchLast((t) => ({ ...t, team: applyProgress(t.team ?? [], ev) })),
      onBom: () => onBomChanged(),
      onFindings: (counts) => {
        patchLast((t) => ({ ...t, review: counts }))
        onReviewRan()
      },
      onDiagram: (mermaid, note) => {
        patchLast((t) => ({ ...t, diagram: { mermaid, note } }))
        onDiagramChanged()
      },
      onFootprint: (id) => patchLast((t) => ({ ...t, footprints: [...(t.footprints ?? []), id] })),
      onNotice: (text) => patchLast((t) => ({ ...t, notices: [...(t.notices ?? []), text] })),
      onRateLimit: (info) => patchLast((t) => ({ ...t, rateLimit: info })),
      onUsage: (usage) => patchLast((t) => ({ ...t, usage })),
      onDone: () => {
        setBusy(false)
        loadSessions()
      },
      onError: (d, info) => {
        patchLast((t) => ({ ...t, error: d, errorInfo: info }))
        setBusy(false)
      },
    }, ctl.signal)
  }

  // A spent subscription: send this vendor's models through its API key instead.
  async function useApiRoute() {
    const vendor = modelInfo?.vendor
    if (vendor !== 'claude' && vendor !== 'openai') return
    const current = await window.pcb.getEngines()
    await window.pcb.saveEngines({ ...current, preferredRoute: { ...current.preferredRoute, [vendor]: 'api' } })
    onRoutesChanged()
  }

  return (
    <div style={{ display: 'flex', height: '100%', flex: 1, minWidth: 0, gap: 0 }}>
      {/* session sidebar */}
      <aside
        style={{
          width: sidebarW,
          flexShrink: 0,
          paddingRight: 8,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* A reply streams into the chat it started in: stay there until it ends. */}
        <button
          onClick={startNew}
          disabled={busy}
          title={busy ? 'Wait for the reply to finish' : undefined}
          style={{ marginBottom: 10, borderColor: color.copperDim }}
        >
          + New chat
        </button>
        <div style={{ overflow: 'auto', flex: 1 }}>
          {sessions.length === 0 && (
            <p style={{ color: color.inkMuted, fontSize: type.size.xs }}>No chats yet.</p>
          )}
          {sessions.map((s) => {
            const active = s.id === activeId
            return (
              <div
                key={s.id}
                onClick={() => !busy && selectSession(s.id)}
                title={busy && !active ? 'Wait for the reply to finish' : undefined}
                style={{
                  padding: '7px 9px',
                  marginBottom: 4,
                  borderRadius: radius.sm,
                  borderLeft: `2px solid ${active ? color.copper : 'transparent'}`,
                  background: active ? color.copperFaint : 'transparent',
                  cursor: busy ? (active ? 'default' : 'not-allowed') : 'pointer',
                  opacity: busy && !active ? 0.6 : 1,
                  display: 'flex',
                  alignItems: 'baseline',
                  gap: 6,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: type.size.sm,
                      color: active ? color.inkPrimary : color.inkSecondary,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {s.title}
                  </div>
                  <div style={{ fontSize: type.size.xs, color: color.inkMuted }}>{relTime(s.updated_at)}</div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    removeSession(s.id)
                  }}
                  disabled={busy}
                  title="delete chat"
                  style={{ border: 0, background: 'transparent', padding: '0 2px', color: color.inkMuted }}
                >
                  ✕
                </button>
              </div>
            )
          })}
        </div>
      </aside>
      <ResizeHandle
        width={sidebarW}
        onChange={(w, done) => {
          setSidebarW(w)
          if (done) saveSidebar(w)
        }}
      />

      {/* conversation */}
      {/* thread, then the composer dock, then a spacer: a new chat gives the thread
          and the spacer equal room so greeting + composer sit centred; a running
          conversation lets the thread take it all and the composer docks below */}
      <div ref={mainRef} className={empty ? 'chat-main empty' : 'chat-main'}>
        {/* scroller spans the pane (scrollbar at the edge); content sits in the column */}
        <div ref={threadRef} className="chat-thread">
          <div ref={columnRef} style={COLUMN}>
            {empty && (
              <div className="greeting">
                <LogoMark size={46} />
                <h1>{greeting}</h1>
              </div>
            )}
            {turns.map((t, i) => (
              <div key={i} style={{ marginBottom: 18 }}>
                {/* what you attached — above your message, same side */}
                {t.images && t.images.length > 0 && (
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      justifyContent: 'flex-end',
                      gap: 6,
                      marginBottom: t.q ? 6 : 0,
                    }}
                  >
                    {t.images.map((im) => (
                      <Thumb key={im.id} name={im.name} url={im.url} size={112} onOpen={() => setPreview(im)} />
                    ))}
                  </div>
                )}

                {/* you — copper, right (an image-only turn has no bubble) */}
                <div style={{ display: t.q ? 'flex' : 'none', justifyContent: 'flex-end' }}>
                  <div
                    style={{
                      background: color.copperFaint,
                      color: color.inkPrimary,
                      border: `1px solid ${color.copperDim}`,
                      padding: '7px 11px',
                      borderRadius: 12,
                      borderTopRightRadius: 3,
                      maxWidth: '75%',
                      whiteSpace: 'pre-wrap',
                      fontSize: type.size.base,
                    }}
                  >
                    {t.q}
                  </div>
                </div>

                {/* reasoning — collapsed, faded; live until the answer starts streaming */}
                {t.thinking && (
                  <ThinkingBlock text={t.thinking} live={live(i) && !t.a} />
                )}

                {/* agent tool trace — purple console lines */}
                {t.tools.length > 0 && (
                  <div style={{ marginTop: 8, fontFamily: type.mono, fontSize: type.size.xs }}>
                    {t.tools.map((tv, j) => (
                      <div key={j} style={{ color: tv.kind === 'call' ? color.agent : color.inkSecondary }}>
                        {tv.kind === 'call' ? '▸ ' : '  ✓ '}
                        {tv.name}
                        {tv.kind === 'result' && tv.info ? ` — ${tv.info.split('\n')[0].slice(0, 60)}` : ''}
                      </div>
                    ))}
                  </div>
                )}

                {/* review team — one row per reviewer, live while they work */}
                {t.team && t.team.length > 0 && <ReviewTeam rows={t.team} live={live(i)} />}

                {/* review receipt — deep-links to the canonical Findings report */}
                {t.review && (
                  <button
                    onClick={onOpenFindings}
                    style={{
                      marginTop: 8,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      background: color.benchRaised,
                      borderColor: color.benchLineStrong,
                      padding: '8px 12px',
                      textAlign: 'left',
                    }}
                  >
                    <span style={{ fontFamily: type.mono, fontSize: type.size.sm }}>
                      {t.review === 'ran' ? (
                        'review complete'
                      ) : (
                        <>
                          <span style={{ color: color.statusCritical }}>{t.review.must_fix} must-fix</span>
                          {'   '}
                          <span style={{ color: color.statusCaution }}>{t.review.verify} verify</span>
                          {'   '}
                          <span style={{ color: color.inkSecondary }}>{t.review.cosmetic} cosmetic</span>
                        </>
                      )}
                    </span>
                    <span style={{ color: color.copper, fontSize: type.size.xs }}>open in Findings →</span>
                  </button>
                )}

                {/* diagram card — thumbnail of the redraw, deep-links to the Diagram tab */}
                {t.diagram && (
                  <button
                    onClick={onOpenDiagram}
                    style={{
                      marginTop: 8,
                      display: 'block',
                      width: 320,
                      maxWidth: '100%',
                      background: color.benchRaised,
                      borderColor: color.benchLineStrong,
                      padding: 10,
                      textAlign: 'left',
                    }}
                  >
                    {t.diagram.mermaid && (
                      <div
                        style={{
                          background: color.benchBase,
                          border: `1px solid ${color.benchLine}`,
                          borderRadius: radius.sm,
                          padding: 6,
                          marginBottom: 8,
                        }}
                      >
                        <MermaidView code={t.diagram.mermaid} compact />
                      </div>
                    )}
                    <div style={{ fontFamily: type.mono, fontSize: type.size.sm, color: color.inkPrimary }}>
                      diagram updated
                    </div>
                    {t.diagram.note && (
                      <div style={{ fontSize: type.size.xs, color: color.inkSecondary, marginTop: 2 }}>{t.diagram.note}</div>
                    )}
                    <div style={{ color: color.copper, fontSize: type.size.xs, marginTop: 6 }}>open in Diagram →</div>
                  </button>
                )}

                {t.footprints?.map((id) => <FootprintCard key={id} id={id} />)}

                {(t.a || (live(i) && !t.error) || (!t.error && !t.stopped && t.tools.length === 0)) && (
                  <AgentReply text={t.a} live={live(i)} />
                )}
                {t.stopped && (
                  <div style={{ marginTop: 6, fontSize: type.size.xs, color: color.inkMuted }}>Stopped.</div>
                )}
                {t.notices?.map((n, k) => (
                  <div key={k} style={{ marginTop: 6, fontSize: type.size.xs, color: color.inkMuted }}>
                    {n}
                  </div>
                ))}
                {t.rateLimit?.utilization != null && !t.error && (
                  <div style={{ marginTop: 6, fontSize: type.size.xs, color: color.inkMuted }}>
                    Subscription · {Math.round(t.rateLimit.utilization * 100)}% of the usage window used
                    {t.rateLimit.resets_at ? ` · resets ${resetTime(t.rateLimit.resets_at)}` : ''}
                  </div>
                )}
                {t.usage && !t.error && (
                  <div
                    style={{ marginTop: 4, fontSize: type.size.xs, color: color.inkMuted }}
                    title={`Largest single request: ${compact(t.usage.max_context)} tokens of input`}
                  >
                    {t.usage.requests} model call{t.usage.requests === 1 ? '' : 's'} ·{' '}
                    {usageLine({
                      tokens_in: t.usage.tokens_fresh + t.usage.tokens_cache_write + t.usage.tokens_cached,
                      tokens_cached: t.usage.tokens_cached,
                      tokens_cache_write: t.usage.tokens_cache_write,
                      tokens_out: t.usage.tokens_out,
                    })}
                  </div>
                )}
                {t.error && t.errorInfo?.code === 'usage_limit' ? (
                  <UsageLimitCard
                    resetsAt={t.errorInfo.resets_at ?? null}
                    canSwitch={!!modelInfo?.routes?.api}
                    onSwitch={useApiRoute}
                    onManage={onManageModels}
                  />
                ) : t.error && (
                  <div
                    style={{
                      marginTop: 8,
                      color: color.statusCritical,
                      fontSize: type.size.sm,
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {t.error}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div ref={dockRef} className="chat-dock" style={COLUMN}>
          {/* one box: attached images on top, then the text field, then its tools
              (attach, send); drop images anywhere on it */}
          <div
            className={dropping ? 'composer drop' : 'composer'}
            onDragOver={(e) => {
              if (!e.dataTransfer.types.includes('Files')) return
              e.preventDefault()
              setDropping(true)
            }}
            onDragLeave={() => setDropping(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDropping(false)
              addFiles(e.dataTransfer.files)
            }}
          >
            {attachments.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '10px 10px 0' }}>
                {attachments.map((a) => (
                  <Thumb
                    key={a.key}
                    name={a.file.name}
                    url={a.url}
                    onOpen={() => setPreview({ name: a.file.name, url: a.url })}
                    onRemove={() => removeAttachment(a.key)}
                  />
                ))}
              </div>
            )}
            {/* Enter sends; Shift+Enter adds a line (skipped mid-IME composition);
                Esc stops a reply in flight. */}
            <textarea
              ref={inputRef}
              rows={1}
              style={{ resize: 'none', lineHeight: type.line.base, overflowY: 'hidden' }}
              placeholder={empty ? 'Ask about parts, the schematic, or the BOM…' : 'Reply…'}
              aria-label="Message"
              value={msg}
              onChange={(e) => setMsg(e.target.value)}
              onPaste={(e) => {
                const files = [...e.clipboardData.files]
                if (files.length === 0) return // plain text: let it paste
                e.preventDefault()
                addFiles(files)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  send()
                } else if (e.key === 'Escape' && busy) {
                  e.preventDefault()
                  stop()
                }
              }}
            />
            <div className="composer-tools">
              <button
                className="composer-icon"
                onClick={() => fileRef.current?.click()}
                disabled={!!attachBlock}
                title={attachBlock || 'Attach images'}
                aria-label="Attach images"
              >
                <Plus size={17} strokeWidth={1.75} />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept={CHAT_IMAGE_TYPES.join(',')}
                multiple
                hidden
                onChange={(e) => {
                  addFiles(e.target.files ?? [])
                  e.target.value = '' // so the same file can be picked again
                }}
              />
              <button
                className={busy ? 'composer-send stop' : 'composer-send'}
                onClick={busy ? stop : send}
                disabled={!busy && (!!sendBlock || (!msg.trim() && attachments.length === 0))}
                title={busy ? 'Stop the reply (Esc)' : sendBlock || 'Send (Enter)'}
                aria-label={busy ? 'Stop' : 'Send'}
              >
                {busy ? (
                  <Square size={12} strokeWidth={0} fill="currentColor" />
                ) : (
                  <SendHorizontal size={15} strokeWidth={1.75} />
                )}
              </button>
            </div>
          </div>

          {/* under the box: what can't be sent (or a reminder to check), then the
              model and effort the next message goes out with */}
          <div className="composer-meta">
            {sendBlock ? (
              <span className="composer-note" style={{ color: color.inkSecondary }}>
                {sendBlock}
                <button onClick={onManageModels} style={{ marginLeft: 8 }}>
                  {modelInfo ? 'Change model' : 'Add a model'}
                </button>
              </span>
            ) : (
              !empty && <span className="composer-note">Ohmen can be wrong — check the cited datasheet.</span>
            )}
            <span className="chips">
              <ModelPicker variant="chip" placement={empty ? 'auto' : 'up'} state={models} onManage={onManageModels} />
              <EffortPicker
                value={effort}
                onChange={(e) => {
                  setEffort(e)
                  setEffortState(e)
                }}
                disabled={!modelInfo?.supports_thinking}
                model={modelInfo?.model}
                placement={empty ? 'auto' : 'up'}
              />
            </span>
          </div>
          {preview && <Lightbox name={preview.name} url={preview.url} onClose={closePreview} />}
          {attachError && (
            <div role="alert" style={{ marginTop: 6, color: color.statusCritical, fontSize: type.size.sm }}>
              {attachError}
            </div>
          )}
        </div>
        <div className="chat-spacer" />
        {exitGreeting && (
          <div
            className="greeting greet-out"
            aria-hidden
            style={exitGreeting.style}
            onAnimationEnd={() => setExitGreeting(null)}
          >
            <LogoMark size={46} />
            <h1>{exitGreeting.text}</h1>
          </div>
        )}
      </div>
    </div>
  )
}
