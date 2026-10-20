import { Children, useEffect, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { getKicadCli, getServices, testDigikey, type KicadCliStatus, type ServiceStatus } from '../api'
import type { ModelState } from '../hooks/useModel'
import { REVIEW_DEPTHS, getReviewDepth, setReviewDepth, type ReviewDepth } from '../reviewDepth'
import { color, radius, type } from '../theme'
import { ProviderCards, ipcMessage } from './ProviderCards'
import { EngineCard, FallbackSetting, RouteSetting, useEngines } from './Subscriptions'

export type SettingsTab = 'general' | 'providers' | 'parts' | 'advanced' | 'about'

const TABS: { id: SettingsTab; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'providers', label: 'Model providers' },
  { id: 'parts', label: 'Parts sources' },
  { id: 'advanced', label: 'Advanced' },
  { id: 'about', label: 'About' },
]

// App settings: a left rail of sections. The gear opens General; "Manage
// providers…" (picker, chat) opens straight on Model providers.
export function SettingsModal({
  state,
  initialTab,
  onClose,
}: {
  state: ModelState
  initialTab: SettingsTab
  onClose: () => void
}) {
  const [tab, setTab] = useState<SettingsTab>(initialTab)

  // Play the dialog out (.settle-out / .scrim-out) before the parent unmounts it.
  const [closing, setClosing] = useState(false)
  function close() {
    if (closing) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return onClose()
    setClosing(true)
    setTimeout(onClose, 140)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  return (
    <div
      onMouseDown={(e) => e.target === e.currentTarget && close()}
      className={closing ? 'scrim-out' : 'scrim-in'}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 900,
        background: 'rgba(10, 15, 13, 0.8)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px 16px',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className={closing ? 'settle-out' : 'settle'}
        style={{
          width: 800,
          maxWidth: '100%',
          // Fixed height: switching sections never resizes the dialog, and in a
          // short window the section body scrolls inside it, not the whole app.
          height: 'min(640px, 100%)',
          background: color.benchBase,
          border: `1px solid ${color.benchLineStrong}`,
          borderRadius: radius.lg,
          display: 'grid',
          gridTemplateColumns: '180px minmax(0, 1fr)',
          gridTemplateRows: 'minmax(0, 1fr)',
          overflow: 'hidden',
        }}
      >
        <nav
          role="tablist"
          aria-orientation="vertical"
          aria-label="Settings sections"
          style={{
            borderRight: `1px solid ${color.benchLine}`,
            background: color.benchVoid,
            padding: '20px 10px',
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          }}
        >
          <h2
            id="settings-title"
            style={{ margin: '0 8px 12px', fontSize: type.size.lg, fontWeight: type.weight.semi }}
          >
            Settings
          </h2>
          {TABS.map((t) => {
            const active = t.id === tab
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                onClick={() => setTab(t.id)}
                style={{
                  // About isn't a setting: it sits at the foot of the rail.
                  marginTop: t.id === 'about' ? 'auto' : undefined,
                  textAlign: 'left',
                  border: 0,
                  borderRadius: radius.sm,
                  padding: '6px 8px',
                  background: active ? color.copperFaint : 'transparent',
                  color: active ? color.copperBright : color.inkSecondary,
                  fontWeight: active ? type.weight.med : type.weight.reg,
                }}
              >
                {t.label}
              </button>
            )
          })}
        </nav>

        <div role="tabpanel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', padding: '16px 16px 12px 20px' }}>
            <h3 style={{ margin: 0, fontSize: type.size.md, fontWeight: type.weight.semi }}>
              {TABS.find((t) => t.id === tab)?.label}
            </h3>
            <span style={{ flex: 1 }} />
            <button
              onClick={close}
              aria-label="Close settings"
              style={{ border: 0, background: 'transparent', padding: 4, color: color.inkSecondary }}
            >
              <X size={18} strokeWidth={1.75} />
            </button>
          </div>
          {/* key: each section starts scrolled to the top */}
          <div
            key={tab}
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: 'auto',
              scrollbarGutter: 'stable',
              padding: '0 10px 20px 20px',
              display: 'grid',
              gap: 14,
              alignContent: 'start',
            }}
          >
            {tab === 'general' && <GeneralSection />}
            {tab === 'providers' && <ProvidersSection state={state} />}
            {tab === 'parts' && <PartsSection />}
            {tab === 'advanced' && <AdvancedSection state={state} />}
            {tab === 'about' && <AboutSection />}
          </div>
        </div>
      </div>
    </div>
  )
}

function ProvidersSection({ state }: { state: ModelState }) {
  return (
    <>
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>
        Bring your own key. Keys are encrypted on this computer and only sent to the provider they belong to.
        Models appear in the picker as soon as a key checks out.
      </div>
      <ProviderCards statuses={state.statuses} onChanged={state.reload} />
    </>
  )
}

// A titled settings group; each child setting sits in its own bubble.
function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ display: 'grid', gap: 8 }}>
      <div
        style={{
          fontSize: type.size.xs,
          color: color.inkMuted,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}
      >
        {title}
      </div>
      {Children.toArray(children).map((child, i) => (
        <div
          key={i}
          style={{
            border: `1px solid ${color.benchLine}`,
            borderRadius: radius.md,
            background: color.benchRaised,
            padding: '12px 14px',
            display: 'grid',
            gap: 10,
          }}
        >
          {child}
        </div>
      ))}
    </section>
  )
}

function GeneralSection() {
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <Group title="KiCad">
        <KicadCliSetting />
        <ProjectClickSetting />
        <AutoCheckSetting />
      </Group>
      <Group title="Design review">
        <ReviewDepthSetting />
      </Group>
    </div>
  )
}

// What clicking the project name in the header does.
function ProjectClickSetting() {
  const [mode, setMode] = useState<'folder' | 'kicad'>('folder')
  useEffect(() => {
    window.pcb
      .getSettings()
      .then((s) => setMode(s.projectClick ?? 'folder'))
      .catch(() => {})
  }, [])

  function choose(next: 'folder' | 'kicad') {
    setMode(next)
    window.pcb.setSettings({ projectClick: next }).catch(() => {})
  }

  const options = [
    { id: 'folder', label: 'Open folder' },
    { id: 'kicad', label: 'Open in KiCad' },
  ] as const

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ fontWeight: type.weight.semi }}>Project name click</span>
      <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>in the header</span>
      <span style={{ flex: 1 }} />
      <div role="radiogroup" aria-label="Project name click" style={{ display: 'flex', gap: 4 }}>
        {options.map((o) => (
          <button
            key={o.id}
            role="radio"
            aria-checked={mode === o.id}
            onClick={() => choose(o.id)}
            style={{
              borderColor: mode === o.id ? color.copper : color.benchLine,
              color: mode === o.id ? color.copperBright : color.inkSecondary,
              background: mode === o.id ? color.copperFaint : color.benchRaised,
            }}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// Re-run the free rule checks (ERC, DRC, built-in patterns) whenever the
// schematic changed, checked on returning to the app. Off by default: checks
// otherwise run when asked in chat.
function AutoCheckSetting() {
  const [on, setOn] = useState(false)
  useEffect(() => {
    window.pcb
      .getSettings()
      .then((s) => setOn(!!s.autoCheck))
      .catch(() => {})
  }, [])

  function choose(next: boolean) {
    setOn(next)
    window.pcb.setSettings({ autoCheck: next }).catch(() => {})
  }

  const options = [
    { id: false, label: 'Off' },
    { id: true, label: 'On save' },
  ] as const

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>Auto-check</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>rule checks when the schematic changes</span>
        <span style={{ flex: 1 }} />
        <div role="radiogroup" aria-label="Auto-check" style={{ display: 'flex', gap: 4 }}>
          {options.map((o) => (
            <button
              key={o.label}
              role="radio"
              aria-checked={on === o.id}
              onClick={() => choose(o.id)}
              style={{
                borderColor: on === o.id ? color.copper : color.benchLine,
                color: on === o.id ? color.copperBright : color.inkSecondary,
                background: on === o.id ? color.copperFaint : color.benchRaised,
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ fontSize: type.size.sm, color: color.inkMuted, lineHeight: type.line.sm }}>
        KiCad ERC, DRC and the built-in pattern checks. No AI, no cost. For a design review, ask in chat.
      </div>
    </>
  )
}

// How many reviewers a design review splits the board across. More reviewers
// look closer at each block, at the cost of more LLM calls.
function ReviewDepthSetting() {
  const [depth, setDepth] = useState<ReviewDepth>(getReviewDepth)
  const index = REVIEW_DEPTHS.findIndex((d) => d.id === depth)
  const current = REVIEW_DEPTHS[index]

  function choose(i: number) {
    const next = REVIEW_DEPTHS[i].id
    setDepth(next)
    setReviewDepth(next)
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>Review depth</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>how many reviewers check the board</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: type.size.sm, color: color.copper }}>{current.label}</span>
      </div>
      <input
        type="range"
        min={0}
        max={REVIEW_DEPTHS.length - 1}
        step={1}
        value={index}
        onChange={(e) => choose(Number(e.target.value))}
        aria-label="Review depth"
        aria-valuetext={current.label}
        style={{ width: '100%', accentColor: color.copper }}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: type.size.xs, color: color.inkMuted }}>
        {REVIEW_DEPTHS.map((d, i) => (
          <button
            key={d.id}
            type="button"
            onClick={() => choose(i)}
            style={{
              all: 'unset',
              cursor: 'pointer',
              color: i === index ? color.inkPrimary : color.inkMuted,
            }}
          >
            {d.label}
          </button>
        ))}
      </div>
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>{current.hint}</div>
      <div style={{ fontSize: type.size.xs, color: color.inkMuted, lineHeight: type.line.xs }}>
        Each part audit takes 10–25 model calls with a tool-capable model. Free API tiers with low daily
        limits usually can't finish a review; models without tool use fall back to a single-pass review.
      </div>
    </>
  )
}

// Run models on a subscription the user already has, through its own CLI.
function AdvancedSection({ state }: { state: ModelState }) {
  const engines = useEngines(state)
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>
        Use a subscription you already pay for instead of an API key. The app runs your own installed, signed-in
        CLI; your login stays with the CLI and never passes through this app.
      </div>
      <Group title="Subscriptions">
        <EngineCard id="codex" engines={engines} />
        <EngineCard id="claude" engines={engines} />
      </Group>
      <Group title="Routing">
        <RouteSetting engines={engines} />
        <FallbackSetting engines={engines} />
      </Group>
      {engines.error && <div style={{ fontSize: type.size.sm, color: color.statusCritical }}>{engines.error}</div>}
    </div>
  )
}

function PartsSection() {
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>
        Datasheets come from the schematic's Datasheet field and manufacturer sites first. A distributor
        account fills the gaps by part number, and is what BOM pricing and stock come from.
      </div>
      <Group title="DigiKey">
        <DigikeySetting />
      </Group>
    </div>
  )
}

// A DigiKey developer app (Product Information v4): client id + secret, stored
// encrypted like the model keys. The secret never comes back to this screen.
function DigikeySetting() {
  const [saved, setSaved] = useState<{ clientId: string; last4: string } | null>(null)
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function reload() {
    const s = await window.pcb.savedServices().catch(() => ({}) as Awaited<ReturnType<typeof window.pcb.savedServices>>)
    setSaved(s.digikey ?? null)
    setClientId(s.digikey?.clientId ?? '')
    setStatus((await getServices().catch(() => null))?.digikey ?? null)
  }
  useEffect(() => {
    reload()
  }, [])

  async function save() {
    setBusy(true)
    setMsg(null)
    try {
      await window.pcb.saveService('digikey', { clientId, clientSecret: secret })
      setSecret('')
      await reload()
      const t = await testDigikey()
      setMsg(t.error ? { ok: false, text: t.error } : { ok: true, text: t.detail ?? 'Signed in to DigiKey.' })
    } catch (e) {
      setMsg({ ok: false, text: ipcMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    setMsg(null)
    try {
      const t = await testDigikey()
      setMsg(t.error ? { ok: false, text: t.error } : { ok: true, text: t.detail ?? 'Signed in to DigiKey.' })
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    await window.pcb.removeService('digikey')
    setMsg(null)
    await reload()
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>Product Information API</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: type.size.sm }}>
          {status?.configured ? (
            <span style={{ color: color.statusVerified }}>
              Connected<span style={{ color: color.inkMuted }}> · {status.source === 'env' ? 'from .env (dev)' : `secret …${saved?.last4 ?? ''}`}</span>
            </span>
          ) : (
            <span style={{ color: color.inkMuted }}>Not connected</span>
          )}
        </span>
      </div>
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>
        Create an app at developer.digikey.com (Production, Product Information v4) and paste its credentials.
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!busy && clientId.trim() && secret.trim()) save()
        }}
        style={{ display: 'grid', gap: 8 }}
      >
        <input
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          placeholder="Client ID"
          aria-label="DigiKey client ID"
          spellCheck={false}
          style={{ fontFamily: type.mono }}
        />
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder={saved ? 'Client secret (saved — enter to replace)' : 'Client secret'}
          aria-label="DigiKey client secret"
          spellCheck={false}
          style={{ fontFamily: type.mono }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button type="submit" disabled={busy || !clientId.trim() || !secret.trim()}>
            {busy ? 'Checking…' : 'Save'}
          </button>
          {status?.configured && (
            <button type="button" onClick={test} disabled={busy}>
              Test
            </button>
          )}
          {saved && (
            <button type="button" onClick={remove} disabled={busy}>
              Remove
            </button>
          )}
          {msg && (
            <span style={{ fontSize: type.size.sm, color: msg.ok ? color.statusVerified : color.statusCritical }}>
              {msg.text}
            </span>
          )}
        </div>
      </form>
    </>
  )
}

const REPO_URL = 'https://github.com/R0mba-Na11avan/Ohmen'

function AboutSection() {
  const [info, setInfo] = useState<{ version: string; electron: string; logPath: string | null; keysProtected: boolean } | null>(null)

  useEffect(() => {
    window.pcb.appInfo().then(setInfo).catch(() => {})
  }, [])

  return (
    <div style={{ display: 'grid', gap: 6, fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>
      <div style={{ fontSize: type.size.base }}>
        <span style={{ color: color.inkPrimary, fontWeight: type.weight.semi }}>Ohmen</span>
        {info && <span style={{ fontFamily: type.mono, color: color.inkSecondary }}> {info.version}</span>}
      </div>
      <div>A design-review copilot for KiCad. Reads your project, never edits it.</div>
      <div>
        Open source under the MIT license.{' '}
        <a href={REPO_URL} target="_blank" rel="noreferrer" style={{ color: color.copper }}>
          Source, issues and releases
        </a>
      </div>
      {info?.logPath && (
        <div style={{ color: color.inkMuted }}>
          Log file: <span style={{ fontFamily: type.mono }}>{info.logPath}</span>
        </div>
      )}
      {info && <div style={{ color: color.inkMuted }}>Electron {info.electron}</div>}
    </div>
  )
}

const SOURCE_LABEL: Record<NonNullable<KicadCliStatus['source']>, string> = {
  custom: 'set by you',
  env: 'from the KICAD_CLI variable',
  path: 'found on PATH',
  auto: 'found automatically',
}

// kicad-cli turns the schematic into a netlist. Auto-detect covers standard
// installs; this is for portable or non-standard ones.
function KicadCliSetting() {
  const [status, setStatus] = useState<KicadCliStatus | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    getKicadCli()
      .then((s) => {
        setStatus(s)
        if (s.source === 'custom' && s.path) setDraft(s.path)
      })
      .catch(() => {})
  }, [])

  async function apply(path: string) {
    setErr('')
    setBusy(true)
    try {
      const s = await window.pcb.setKicadCli(path)
      setStatus(s)
      setDraft(s.source === 'custom' && s.path ? s.path : '')
    } catch (e) {
      setErr(ipcMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function browse() {
    const picked = await window.pcb.pickKicadCli()
    if (picked) {
      setDraft(picked)
      apply(picked)
    }
  }

  const custom = status?.source === 'custom'
  const dirty = draft.trim() !== (custom ? status?.path ?? '' : '')

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>kicad-cli</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>exports the schematic for reading</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: type.size.sm }}>
          {!status ? (
            <span style={{ color: color.inkMuted }}>Checking…</span>
          ) : status.version ? (
            <span style={{ color: color.statusVerified }}>
              KiCad {status.version}
              <span style={{ color: color.inkMuted }}> · {status.source && SOURCE_LABEL[status.source]}</span>
            </span>
          ) : (
            <span style={{ color: color.statusCritical }}>{status.error ?? 'Not found'}</span>
          )}
        </span>
      </div>
      {status?.path && (
        <div
          title={status.path}
          style={{
            fontFamily: type.mono,
            fontSize: type.size.sm,
            color: color.inkSecondary,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {status.path}
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (dirty && !busy) apply(draft)
        }}
        style={{ display: 'flex', gap: 8, alignItems: 'center' }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Auto-detect (leave empty)"
          aria-label="kicad-cli path"
          spellCheck={false}
          style={{ flex: 1, minWidth: 0, fontFamily: type.mono }}
        />
        <button type="button" onClick={browse} disabled={busy}>
          Browse…
        </button>
        <button type="submit" disabled={!dirty || busy} style={{ borderColor: dirty ? color.copperDim : undefined }}>
          {busy ? 'Checking…' : 'Save'}
        </button>
        {custom && (
          <button type="button" disabled={busy} onClick={() => apply('')}>
            Use auto-detect
          </button>
        )}
      </form>
      {err && <div style={{ fontSize: type.size.sm, color: color.statusCritical }}>{err}</div>}
    </>
  )
}
