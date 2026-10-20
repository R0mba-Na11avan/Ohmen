import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, RefreshCw, Star } from 'lucide-react'
import { refreshModels, type ModelInfo, type ProviderId, type Route } from '../api'
import type { ModelState } from '../hooks/useModel'
import { color, radius, type } from '../theme'
import { AnchoredPopover } from './Popover'
import { PROVIDER_META, PROVIDER_ORDER, localEndpointLabel } from './ProviderCards'

function Badge({ children, tone = color.inkMuted }: { children: string; tone?: string }) {
  return (
    <span
      style={{
        fontSize: type.size.xs,
        lineHeight: type.line.xs,
        color: tone,
        border: `1px solid ${color.benchLine}`,
        borderRadius: radius.sm,
        padding: '0 5px',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}

// Capabilities live in the tooltip; the row itself only flags what changes behaviour.
function describe(m: ModelInfo): string {
  const caps = [m.supports_pdf && 'pdf', m.supports_vision && 'vision', m.cost_tier !== 'unknown' && `${m.cost_tier} cost`]
  return [caps.filter(Boolean).join(' · '), m.notes].filter(Boolean).join('\n')
}

// Every spec a row stands for (its API and subscription routes), for matching
// the saved pick, stars and recents whichever route they were saved under.
export const specsOf = (m: ModelInfo) => [m.spec, ...Object.values(m.routes ?? {})] as string[]

function routeLabel(m: ModelInfo, route: Route): string {
  if (route === 'api') return 'API key'
  return m.vendor === 'claude' ? 'Claude Code sub' : 'Codex sub'
}

// One badge per way to run the model; the one this row would use now is lit.
function RouteBadges({ m }: { m: ModelInfo }) {
  const routes = (['api', 'sub'] as Route[]).filter((r) => m.routes?.[r])
  if (routes.length === 0 || (routes.length === 1 && routes[0] === 'api')) return null
  return (
    <>
      {routes.map((r) => (
        <Badge key={r} tone={m.routes[r] === m.spec ? color.copper : color.inkMuted}>
          {routeLabel(m, r)}
        </Badge>
      ))}
    </>
  )
}

function Row({
  m,
  active,
  starred,
  detailed,
  onPick,
  onStar,
}: {
  m: ModelInfo
  active: boolean
  starred: boolean
  detailed: boolean
  onPick: () => void
  onStar: () => void
}) {
  return (
    <div
      className="model-row"
      style={{
        display: 'flex',
        alignItems: 'center',
        borderRadius: radius.sm,
        background: active ? color.copperFaint : 'transparent',
      }}
    >
      <button
        role="option"
        aria-selected={active}
        onClick={onPick}
        title={describe(m) || undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          flex: 1,
          minWidth: 0,
          textAlign: 'left',
          border: 0,
          padding: '5px 4px 5px 8px',
          background: 'transparent',
          color: active ? color.copperBright : color.inkPrimary,
        }}
      >
        <span style={{ flex: 1, minWidth: 0, fontFamily: type.mono, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {m.model}
        </span>
        {m.recommended && <Badge tone={color.copper}>recommended</Badge>}
        <RouteBadges m={m} />
        {detailed && m.supports_pdf && <Badge>pdf</Badge>}
        {detailed && m.supports_vision && <Badge>vision</Badge>}
        {!m.supports_tools && <Badge tone={color.statusCaution}>no tools</Badge>}
        {detailed && m.cost_tier !== 'unknown' && m.cost_tier !== 'subscription' && <Badge>{m.cost_tier}</Badge>}
      </button>
      <button
        className="model-star"
        onClick={onStar}
        aria-pressed={starred}
        aria-label={starred ? `Unstar ${m.model}` : `Star ${m.model}`}
        title={starred ? 'Unstar' : 'Star — always show in the short list'}
        style={{
          display: 'grid',
          placeItems: 'center',
          border: 0,
          background: 'transparent',
          padding: '4px 8px',
          color: starred ? color.copper : color.inkMuted,
        }}
      >
        <Star size={13} strokeWidth={1.75} fill={starred ? 'currentColor' : 'none'} />
      </button>
    </div>
  )
}

// Providers with this many models or fewer are never collapsed.
const COLLAPSE_ABOVE = 4
// A provider with nothing recommended/recent/starred still shows a few.
const FALLBACK_COUNT = 3

// Model switch: a button showing the active model, opening a list grouped by
// provider (only providers with a working key appear). Each group shows a short
// list (active, starred, recent, recommended) with the rest one click away; search
// always covers every model. `bar` sits in the top corner (start screen); `chip`
// sits under the chat composer, where the list may open upward.
export function ModelPicker({
  state,
  onManage,
  variant = 'bar',
  placement = 'down',
}: {
  state: ModelState
  onManage: () => void
  variant?: 'bar' | 'chip'
  placement?: 'down' | 'up' | 'auto'
}) {
  const { models, model, info, recent, starred, setModel, toggleStar, reload } = state
  const [open, setOpen] = useState(false)
  // Closed but still mounted while the exit animation plays.
  const [closing, setClosing] = useState(false)
  const [q, setQ] = useState('')
  const [expanded, setExpanded] = useState<Set<ProviderId>>(new Set())
  const [refreshing, setRefreshing] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [localBase, setLocalBase] = useState<string | undefined>()

  // Local is a catch-all; name the endpoint actually configured (Ollama, LM Studio…).
  useEffect(() => {
    window.pcb
      .savedProviders()
      .then((s) => setLocalBase(s.local?.baseUrl))
      .catch(() => {})
  }, [models])
  const short = (p: ProviderId) => (p === 'local' ? localEndpointLabel(localBase) : PROVIDER_META[p].family)
  const long = (p: ProviderId) => (p === 'local' ? localEndpointLabel(localBase) : PROVIDER_META[p].name)

  function show() {
    setClosing(false)
    setOpen(true)
  }

  const close = useCallback(() => {
    setOpen(false)
    // Reduced motion disables the animation, so animationend would never come.
    setClosing(!window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  }, [])

  // Each group: the rows to show, plus the provider's total so the toggle can say how many are hidden.
  const needle = q.trim().toLowerCase()
  const groups = useMemo(() => {
    const keep = new Set([model, ...starred, ...recent])
    return PROVIDER_ORDER.flatMap((p) => {
      const all = models.filter((m) => m.vendor === p)
      if (needle) {
        const hits = all.filter((m) => m.spec.toLowerCase().includes(needle))
        return hits.length ? [{ p, shown: hits, total: all.length, collapsible: false }] : []
      }
      if (all.length === 0) return []
      if (all.length <= COLLAPSE_ABOVE || expanded.has(p)) {
        return [{ p, shown: all, total: all.length, collapsible: all.length > COLLAPSE_ABOVE }]
      }
      const short = all.filter((m) => specsOf(m).some((s) => keep.has(s)) || m.recommended)
      return [{ p, shown: short.length ? short : all.slice(0, FALLBACK_COUNT), total: all.length, collapsible: true }]
    })
  }, [models, needle, model, starred, recent, expanded])

  function toggleGroup(p: ProviderId) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })
  }

  async function refresh() {
    setRefreshing(true)
    try {
      await refreshModels()
      await reload()
    } finally {
      setRefreshing(false)
    }
  }

  const label = info ? info.model : models.length ? 'Pick a model' : 'Connect a model'

  const chip = variant === 'chip'
  return (
    <div>
      <button
        ref={triggerRef}
        className={chip ? 'chip' : undefined}
        onClick={() => (!models.length ? onManage() : open ? close() : show())}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={info ? `${long(info.vendor as ProviderId)} · ${info.model}` : undefined}
        style={{ display: 'flex', alignItems: 'center', gap: 6, maxWidth: chip ? 260 : 300 }}
      >
        {info && !chip && (
          <span style={{ color: color.inkMuted, whiteSpace: 'nowrap', flexShrink: 0 }}>{short(info.vendor as ProviderId)}</span>
        )}
        <span
          style={{
            fontFamily: info ? type.mono : type.sans,
            color: info ? color.inkPrimary : color.copper,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </span>
        {models.length > 0 && <ChevronDown size={14} strokeWidth={1.75} style={{ flexShrink: 0 }} />}
      </button>

      <AnchoredPopover
        anchorRef={triggerRef}
        open={open}
        closing={closing}
        onClose={close}
        onExited={() => setClosing(false)}
        prefer={placement}
        width={440}
      >
          <div style={{ padding: 8, borderBottom: `1px solid ${color.benchLine}` }}>
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search models…"
              aria-label="Search models"
              style={{ width: '100%' }}
            />
          </div>
          {/* Gutter always reserved so expanding a group never reflows the rows. */}
          <div role="listbox" style={{ overflowY: 'auto', scrollbarGutter: 'stable', padding: '6px 0 6px 6px', flex: 1 }}>
            {groups.length === 0 && (
              <div style={{ padding: 10, color: color.inkMuted, fontSize: type.size.sm }}>No models match “{q}”.</div>
            )}
            {groups.map(({ p, shown, total, collapsible }) => {
              const isOpen = expanded.has(p)
              return (
                <div key={p} style={{ marginBottom: 6 }}>
                  <div
                    style={{
                      fontSize: type.size.xs,
                      color: color.inkMuted,
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      padding: '6px 8px 2px',
                    }}
                  >
                    {long(p)}
                  </div>
                  {shown.map((m) => (
                    <Row
                      key={m.spec}
                      m={m}
                      active={m.spec === model}
                      starred={specsOf(m).some((s) => starred.includes(s))}
                      detailed={isOpen || !!needle}
                      onPick={() => {
                        setModel(m.spec)
                        close()
                      }}
                      onStar={() => toggleStar(m.spec)}
                    />
                  ))}
                  {collapsible && (isOpen || shown.length < total) && (
                    <button
                      onClick={() => toggleGroup(p)}
                      aria-expanded={isOpen}
                      style={{
                        border: 0,
                        background: 'transparent',
                        padding: '3px 8px',
                        fontSize: type.size.sm,
                        color: color.inkSecondary,
                      }}
                    >
                      {isOpen ? 'Show fewer' : `Show all ${total} models`}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: 8,
              borderTop: `1px solid ${color.benchLine}`,
            }}
          >
            <button
              onClick={() => {
                close()
                onManage()
              }}
            >
              Manage providers…
            </button>
            <span style={{ flex: 1 }} />
            <button
              onClick={refresh}
              disabled={refreshing}
              title="Re-list models from every provider"
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              <RefreshCw size={13} strokeWidth={1.75} className={refreshing ? 'spin' : undefined} />
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
      </AnchoredPopover>
    </div>
  )
}
