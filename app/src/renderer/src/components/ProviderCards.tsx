import { useEffect, useState } from 'react'
import type { LocalOverrides, ProviderId, ProviderStatus, SavedProvider } from '../api'
import { color, radius, type } from '../theme'

// Display facts per provider. `hint` is the one line that helps a first-timer pick.
export const PROVIDER_META: Record<
  ProviderId,
  { name: string; family: string; keyUrl?: string; placeholder: string; hint?: string }
> = {
  gemini: {
    name: 'Google',
    family: 'Gemini',
    keyUrl: 'https://aistudio.google.com/apikey',
    placeholder: 'AIza…',
    hint: 'Free tier available, so this is the easiest place to start.',
  },
  claude: {
    name: 'Anthropic',
    family: 'Claude',
    keyUrl: 'https://console.anthropic.com/settings/keys',
    placeholder: 'sk-ant-…',
    hint: 'Reads datasheet PDFs natively.',
  },
  openai: {
    name: 'OpenAI',
    family: 'GPT',
    keyUrl: 'https://platform.openai.com/api-keys',
    placeholder: 'sk-…',
    hint: 'Chat and parts research. No native datasheet PDFs.',
  },
  local: {
    name: 'Local / OpenAI-compatible',
    family: 'Ollama, LM Studio, OpenRouter…',
    placeholder: 'optional',
    hint: 'Runs on your machine, or any endpoint that speaks the OpenAI API.',
  },
}

export const PROVIDER_ORDER: ProviderId[] = ['gemini', 'claude', 'openai', 'local']

const LOCAL_PRESETS = [
  { label: 'Ollama', url: 'http://localhost:11434/v1' },
  { label: 'LM Studio', url: 'http://localhost:1234/v1' },
  { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
]

// Short name for the local endpoint actually in use: the preset it matches
// (ignoring a trailing slash), else its host (e.g. "groq.com", "localhost:8080").
export function localEndpointLabel(baseUrl?: string): string {
  if (!baseUrl) return 'Local'
  const norm = (u: string) => u.trim().replace(/\/+$/, '').toLowerCase()
  const preset = LOCAL_PRESETS.find((p) => norm(p.url) === norm(baseUrl))
  if (preset) return preset.label
  try {
    return new URL(baseUrl).host.replace(/^api\./, '')
  } catch {
    return 'Local'
  }
}

// ipcRenderer.invoke wraps main-process errors; show only the human part.
export function ipcMessage(e: unknown): string {
  return String(e instanceof Error ? e.message : e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '')
}

type Statuses = Partial<Record<ProviderId, ProviderStatus>>

// All four provider cards. Saving pushes the key through main into the sidecar,
// which validates it by listing models; `onChanged` lets the app re-list models.
export function ProviderCards({ statuses, onChanged }: { statuses: Statuses; onChanged: () => void }) {
  const [saved, setSaved] = useState<Partial<Record<ProviderId, SavedProvider>>>({})

  const [keysProtected, setKeysProtected] = useState(true)

  const refreshSaved = () => window.pcb.savedProviders().then(setSaved).catch(() => {})
  useEffect(() => {
    refreshSaved()
    window.pcb
      .appInfo()
      .then((i) => setKeysProtected(i.keysProtected))
      .catch(() => {})
  }, [])

  const changed = () => {
    refreshSaved()
    onChanged()
  }

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {!keysProtected && (
        <div style={{ fontSize: type.size.sm, color: color.statusCaution, lineHeight: type.line.sm }}>
          No system keyring found, so saved keys are only obfuscated on disk. Install and unlock GNOME
          Keyring or KWallet, then save your keys again.
        </div>
      )}
      {PROVIDER_ORDER.map((p) => (
        <ProviderCard key={p} id={p} status={statuses[p]} saved={saved[p]} onChanged={changed} />
      ))}
    </div>
  )
}

function StatusLine({ status, saved }: { status?: ProviderStatus; saved?: SavedProvider }) {
  if (!status?.configured) return <span style={{ color: color.inkMuted }}>Not connected</span>
  if (!status.ok)
    return <span style={{ color: color.statusCritical, wordBreak: 'break-word' }}>{status.error || 'Not working'}</span>
  const n = status.models.length
  const source =
    status.source === 'env' ? 'key from .env (dev)' : saved?.last4 ? `key ••••${saved.last4}` : 'saved'
  return (
    <span style={{ color: color.statusVerified }}>
      Connected
      <span style={{ color: color.inkMuted }}>
        {' · '}
        {n} model{n === 1 ? '' : 's'} · {source}
      </span>
    </span>
  )
}

function ProviderCard({
  id,
  status,
  saved,
  onChanged,
}: {
  id: ProviderId
  status?: ProviderStatus
  saved?: SavedProvider
  onChanged: () => void
}) {
  const meta = PROVIDER_META[id]
  const isLocal = id === 'local'
  const [key, setKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(saved?.baseUrl ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (saved?.baseUrl) setBaseUrl(saved.baseUrl)
  }, [saved?.baseUrl])

  async function act(fn: () => Promise<unknown>) {
    setErr('')
    setBusy(true)
    try {
      await fn()
      setKey('')
      onChanged()
    } catch (e) {
      setErr(ipcMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const canSave = isLocal ? !!baseUrl.trim() && (baseUrl.trim() !== saved?.baseUrl || !!key.trim()) : !!key.trim()
  const removable = isLocal ? !!saved?.baseUrl : !!saved?.last4

  return (
    <div
      style={{
        border: `1px solid ${status?.configured && status.ok ? color.benchLineStrong : color.benchLine}`,
        borderRadius: radius.md,
        background: color.benchRaised,
        padding: '12px 14px',
        display: 'grid',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: type.weight.semi }}>{meta.name}</span>
        <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>{meta.family}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: type.size.sm }}>
          <StatusLine status={status} saved={saved} />
        </span>
      </div>
      {meta.hint && <div style={{ fontSize: type.size.sm, color: color.inkSecondary }}>{meta.hint}</div>}

      {isLocal && (
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {LOCAL_PRESETS.map((pr) => (
              <button
                key={pr.label}
                onClick={() => setBaseUrl(pr.url)}
                style={{
                  padding: '2px 9px',
                  fontSize: type.size.xs,
                  borderColor: baseUrl === pr.url ? color.copper : color.benchLine,
                }}
              >
                {pr.label}
              </button>
            ))}
          </div>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="Base URL, e.g. http://localhost:11434/v1"
            aria-label={`${meta.name} base URL`}
            style={{ fontFamily: type.mono }}
          />
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (canSave && !busy)
            act(() => window.pcb.saveProvider(id, isLocal ? { baseUrl, apiKey: key } : { apiKey: key }))
        }}
        style={{ display: 'flex', gap: 8, alignItems: 'center' }}
      >
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={
            isLocal ? 'API key (optional)' : saved?.last4 ? `Replace key ••••${saved.last4}` : `API key  ${meta.placeholder}`
          }
          aria-label={`${meta.name} API key`}
          autoComplete="off"
          spellCheck={false}
          style={{ flex: 1, minWidth: 0, fontFamily: type.mono }}
        />
        <button type="submit" disabled={!canSave || busy} style={{ borderColor: canSave ? color.copperDim : undefined }}>
          {busy ? 'Checking…' : 'Save'}
        </button>
        {removable && (
          <button type="button" disabled={busy} onClick={() => act(() => window.pcb.removeProvider(id))}>
            Remove
          </button>
        )}
      </form>

      {meta.keyUrl && (
        <a href={meta.keyUrl} target="_blank" rel="noreferrer" style={{ fontSize: type.size.sm, color: color.copper, justifySelf: 'start' }}>
          Get a {meta.name} key ↗
        </a>
      )}
      {err && <div style={{ fontSize: type.size.sm, color: color.statusCritical }}>{err}</div>}

      {isLocal && status?.ok && status.models.length > 0 && (
        <LocalModelToggles models={status.models} overrides={saved?.overrides ?? {}} onChanged={onChanged} />
      )}
    </div>
  )
}

// Local models can't be profiled, so the user says which ones can call tools
// (needed by the chat agent) or read images, and how big a context they take
// (long chats condense at half of it; 8k is assumed when blank).
function LocalModelToggles({
  models,
  overrides,
  onChanged,
}: {
  models: string[]
  overrides: LocalOverrides
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function save(model: string, change: LocalOverrides[string]) {
    setBusy(true)
    try {
      await window.pcb.setLocalOverrides({ ...overrides, [model]: { ...overrides[model], ...change } })
      onChanged()
    } finally {
      setBusy(false)
    }
  }
  const toggle = (model: string, flag: 'tools' | 'vision', on: boolean) => save(model, { [flag]: on })

  return (
    <div style={{ borderTop: `1px solid ${color.benchLine}`, paddingTop: 8, display: 'grid', gap: 4 }}>
      <div style={{ fontSize: type.size.xs, color: color.inkMuted }}>
        Tick what each model can do. The chat copilot needs tool calling. Context is the window your runtime
        runs the model with, in k tokens.
      </div>
      <div style={{ maxHeight: 180, overflowY: 'auto', display: 'grid', gap: 2 }}>
        {models.map((m) => (
          <div key={m} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: type.size.sm }}>
            <span style={{ flex: 1, minWidth: 0, fontFamily: type.mono, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {m}
            </span>
            {(['tools', 'vision'] as const).map((flag) => (
              <label key={flag} style={{ display: 'flex', gap: 4, alignItems: 'center', color: color.inkSecondary }}>
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={!!overrides[m]?.[flag]}
                  onChange={(e) => toggle(m, flag, e.target.checked)}
                />
                {flag}
              </label>
            ))}
            <label style={{ display: 'flex', gap: 4, alignItems: 'center', color: color.inkSecondary }}>
              <input
                type="number"
                min={2}
                max={2000}
                step={1}
                disabled={busy}
                placeholder="8"
                defaultValue={overrides[m]?.context ? Math.round(overrides[m].context! / 1000) : ''}
                onBlur={(e) => {
                  const k = Number(e.target.value)
                  const context = k >= 2 ? Math.round(k * 1000) : undefined
                  if (context !== overrides[m]?.context) save(m, { context })
                }}
                aria-label={`${m} context window, thousands of tokens`}
                style={{ width: 52 }}
              />
              k ctx
            </label>
          </div>
        ))}
      </div>
    </div>
  )
}
