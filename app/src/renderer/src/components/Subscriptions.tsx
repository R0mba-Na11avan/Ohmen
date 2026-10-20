import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  getEngineStatus,
  startEngineLogin,
  type EngineId,
  type EngineSettings,
  type EngineStatus,
  type Route,
} from '../api'
import type { ModelState } from '../hooks/useModel'
import { color, radius, type } from '../theme'
import { ipcMessage } from './ProviderCards'

// Run models on a subscription the user already pays for: the app drives their
// own logged-in CLI (Codex, Claude Code) and never sees the login itself.

const ENGINE_META: Record<EngineId, { name: string; plan: string; install: string; login: string }> = {
  codex: {
    name: 'Codex',
    plan: 'ChatGPT subscription',
    install: 'npm i -g @openai/codex',
    login: 'codex login',
  },
  claude: {
    name: 'Claude Code',
    plan: 'Claude Pro / Max subscription',
    install: 'npm i -g @anthropic-ai/claude-code',
    login: 'claude auth login',
  },
}

export interface EnginesState {
  settings: EngineSettings
  status: Partial<Record<EngineId, EngineStatus>>
  busy: boolean
  error: string | null
  save: (patch: EngineSettings) => Promise<void>
  recheck: () => Promise<void>
}

// Settings live in main (settings.json); status comes from the sidecar's CLI detection.
export function useEngines(state: ModelState): EnginesState {
  const [settings, setSettings] = useState<EngineSettings>({})
  const [status, setStatus] = useState<Partial<Record<EngineId, EngineStatus>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    window.pcb.getEngines().then(setSettings).catch(() => {})
    getEngineStatus().then(setStatus).catch(() => {})
  }, [])

  const save = useCallback(
    async (patch: EngineSettings) => {
      const next: EngineSettings = {
        ...settings,
        ...patch,
        codex: { ...settings.codex, ...patch.codex },
        claude: { ...settings.claude, ...patch.claude },
        preferredRoute: { ...settings.preferredRoute, ...patch.preferredRoute },
      }
      setSettings(next)
      setBusy(true)
      setError(null)
      try {
        setStatus(await window.pcb.saveEngines(next))
        await state.reload()
      } catch (e) {
        setError(ipcMessage(e))
      } finally {
        setBusy(false)
      }
    },
    [settings, state],
  )

  const recheck = useCallback(async () => {
    setBusy(true)
    try {
      setStatus(await getEngineStatus(true))
      await state.reload()
    } finally {
      setBusy(false)
    }
  }, [state])

  return { settings, status, busy, error, save, recheck }
}

function Mono({ children }: { children: ReactNode }) {
  return (
    <code
      style={{
        fontFamily: type.mono,
        fontSize: type.size.sm,
        background: color.benchBase,
        border: `1px solid ${color.benchLine}`,
        borderRadius: radius.sm,
        padding: '1px 5px',
      }}
    >
      {children}
    </code>
  )
}

// Installed? Logged in? On which plan? Plus the next step when something's missing.
export function EngineStatusLine({ id, st }: { id: EngineId; st: EngineStatus | undefined }) {
  const meta = ENGINE_META[id]
  if (!st) return <span style={{ color: color.inkMuted }}>Checking…</span>
  if (!st.installed) {
    return (
      <span style={{ color: color.inkSecondary, lineHeight: type.line.sm }}>
        Not installed. In a terminal: <Mono>{meta.install}</Mono>, then <Mono>{meta.login}</Mono>.
      </span>
    )
  }
  const tone = st.logged_in ? color.statusVerified : color.statusCaution
  return (
    <span style={{ lineHeight: type.line.sm }}>
      <span style={{ color: tone }}>{st.logged_in ? 'Signed in' : st.logged_in === false ? 'Not signed in' : 'Installed'}</span>
      <span style={{ color: color.inkMuted }}>
        {' · '}
        {st.detail || meta.plan}
        {st.version ? ` · v${st.version}` : ''}
      </span>
    </span>
  )
}

function Toggle({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1 }}>
      <input
        type="checkbox"
        checked={on}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: color.copper }}
      />
      {label}
    </label>
  )
}

// One engine: status, on/off, CLI location, sign in. Claude Code also asks the
// user to accept the terms risk before it can be switched on.
export function EngineCard({ id, engines }: { id: EngineId; engines: EnginesState }) {
  const meta = ENGINE_META[id]
  const st = engines.status[id]
  const cfg = engines.settings[id] ?? {}
  const tosAck = id === 'claude' ? !!engines.settings.claude?.tosAck : true
  const [path, setPath] = useState(cfg.path ?? '')
  const [loginMsg, setLoginMsg] = useState<string | null>(null)

  useEffect(() => setPath(cfg.path ?? ''), [cfg.path])

  async function login() {
    setLoginMsg(null)
    try {
      await startEngineLogin(id)
      setLoginMsg('Finish signing in in the terminal window, then press Check again.')
    } catch (e) {
      setLoginMsg(ipcMessage(e))
    }
  }

  async function browse() {
    const picked = await window.pcb.pickEngineCli(id)
    if (picked) {
      setPath(picked)
      await engines.save({ [id]: { ...cfg, path: picked } })
    }
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>{meta.name}</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>{meta.plan}</span>
        {id === 'claude' && (
          <span style={{ fontSize: type.size.xs, color: color.statusCaution, border: `1px solid ${color.statusCaution}`, borderRadius: radius.sm, padding: '0 5px' }}>
            experimental
          </span>
        )}
        <span style={{ flex: 1 }} />
        <Toggle
          on={!!cfg.enabled}
          disabled={engines.busy || !tosAck}
          onChange={(v) => engines.save({ [id]: { ...cfg, enabled: v } })}
          label="Use"
        />
      </div>
      <div style={{ fontSize: type.size.sm }}>
        <EngineStatusLine id={id} st={st} />
      </div>

      {id === 'claude' && (
        <div
          style={{
            fontSize: type.size.sm,
            lineHeight: type.line.sm,
            color: color.inkSecondary,
            background: color.statusCautionFaint,
            border: `1px solid ${color.statusCaution}`,
            borderRadius: radius.md,
            padding: '8px 10px',
            display: 'grid',
            gap: 6,
          }}
        >
          <span>
            Anthropic's Consumer Terms say Claude Pro/Max logins are for Claude Code and claude.ai only. Running
            your installed Claude Code from this app may break those terms, and Anthropic could restrict your
            account. The risk is yours. An Anthropic API key carries no such risk.
          </span>
          <Toggle
            on={tosAck}
            disabled={engines.busy}
            onChange={(v) => engines.save({ claude: { ...cfg, tosAck: v, enabled: v ? cfg.enabled : false } })}
            label="I understand and accept this risk"
          />
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onBlur={() => path !== (cfg.path ?? '') && engines.save({ [id]: { ...cfg, path } })}
          placeholder={st?.path ?? `${id} (found on PATH)`}
          aria-label={`${meta.name} CLI path`}
          spellCheck={false}
          style={{ flex: 1, minWidth: 180, fontFamily: type.mono, fontSize: type.size.sm }}
        />
        <button onClick={browse} disabled={engines.busy}>
          Browse…
        </button>
        {st?.installed && !st.logged_in && (
          <button onClick={login} disabled={engines.busy} style={{ borderColor: color.copper, color: color.copperBright }}>
            Sign in…
          </button>
        )}
        <button onClick={engines.recheck} disabled={engines.busy}>
          {engines.busy ? 'Checking…' : 'Check again'}
        </button>
      </div>
      {loginMsg && <div style={{ fontSize: type.size.sm, color: color.inkSecondary }}>{loginMsg}</div>}
    </>
  )
}

// Per vendor: which route a model uses when both an API key and a subscription serve it.
export function RouteSetting({ engines }: { engines: EnginesState }) {
  const rows: { vendor: 'claude' | 'openai'; label: string; sub: string; engine: EngineId }[] = [
    { vendor: 'openai', label: 'OpenAI models', sub: 'Codex subscription', engine: 'codex' },
    { vendor: 'claude', label: 'Claude models', sub: 'Claude Code subscription', engine: 'claude' },
  ]
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>Preferred route</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>when a model is available both ways</span>
      </div>
      {rows.map((r) => {
        const current: Route = engines.settings.preferredRoute?.[r.vendor] ?? 'api'
        const subOn = !!engines.status[r.engine]?.enabled
        const options: { id: Route; label: string }[] = [
          { id: 'api', label: 'API key' },
          { id: 'sub', label: r.sub },
        ]
        return (
          <div key={r.vendor} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ minWidth: 110, color: color.inkSecondary }}>{r.label}</span>
            <div role="radiogroup" aria-label={`${r.label} route`} style={{ display: 'flex', gap: 4 }}>
              {options.map((o) => {
                const on = current === o.id
                const disabled = engines.busy || (o.id === 'sub' && !subOn)
                return (
                  <button
                    key={o.id}
                    role="radio"
                    aria-checked={on}
                    disabled={disabled}
                    title={disabled && o.id === 'sub' ? `Turn on ${r.sub} above first` : undefined}
                    onClick={() => engines.save({ preferredRoute: { [r.vendor]: o.id } })}
                    style={{
                      borderColor: on ? color.copper : color.benchLine,
                      color: on ? color.copperBright : color.inkSecondary,
                      background: on ? color.copperFaint : color.benchRaised,
                    }}
                  >
                    {o.label}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function FallbackSetting({ engines }: { engines: EnginesState }) {
  return (
    <>
      <Toggle
        on={!!engines.settings.fallbackToApi}
        disabled={engines.busy}
        onChange={(v) => engines.save({ fallbackToApi: v })}
        label="When a subscription hits its usage limit, continue on my API key"
      />
      <div style={{ fontSize: type.size.sm, lineHeight: type.line.sm, color: color.inkSecondary }}>
        Off: the chat or review stops and says when the limit resets. On: it carries on with the same
        vendor’s API key (if one is saved), which is billed per use.
      </div>
    </>
  )
}
