import { useState } from 'react'
import { TriangleAlert } from 'lucide-react'
import { startEngineLogin, type EngineId } from '../api'
import type { ModelState } from '../hooks/useModel'
import { color, radius, space, type } from '../theme'
import { ProviderCards, ipcMessage } from './ProviderCards'
import { EngineStatusLine, useEngines } from './Subscriptions'

type Path = 'key' | EngineId

// First run with no provider connected: connect an API key, or an existing
// ChatGPT (Codex CLI) or Claude (Claude Code) subscription, or skip and look
// around (AI actions stay disabled with an "Add a model" prompt until one is
// connected). The Claude route carries a small warning mark: using a Pro/Max
// login from another app may break Anthropic's terms, and it can't be switched
// on until that risk is accepted.
export function Onboarding({ state, onDone }: { state: ModelState; onDone: () => void }) {
  const connected = state.models.length > 0
  const [path, setPath] = useState<Path>('key')
  const options: { id: Path; label: string; hint: string; caution?: string }[] = [
    { id: 'key', label: 'Use an API key', hint: 'Pay per use with Claude, Gemini, OpenAI or a local model.' },
    { id: 'codex', label: 'Use my ChatGPT subscription', hint: 'Runs on your plan through the Codex CLI.' },
    {
      id: 'claude',
      label: 'Use my Claude subscription',
      hint: 'Runs on your plan through Claude Code. Experimental.',
      caution: "Experimental, and it may break Anthropic's terms. Read the note before switching it on.",
    },
  ]
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: `${space[7]}px ${space[6]}px` }}>
      <div style={{ maxWidth: 620, margin: '0 auto', display: 'grid', gap: space[4] }}>
        <div>
          <div style={{ fontSize: type.size.xl, fontWeight: type.weight.semi, letterSpacing: '-0.02em' }}>
            Connect a model
          </div>
          <div style={{ marginTop: 6, color: color.inkSecondary, lineHeight: type.line.base }}>
            The copilot runs on your own AI account. Pick how to connect; you can add more later from the gear
            icon.
          </div>
        </div>
        <div role="radiogroup" aria-label="How to connect" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
          {options.map((o) => {
            const on = path === o.id
            return (
              <button
                key={o.id}
                role="radio"
                aria-checked={on}
                onClick={() => setPath(o.id)}
                style={{
                  position: 'relative',
                  display: 'grid',
                  alignContent: 'start',
                  gap: 4,
                  textAlign: 'left',
                  padding: '10px 12px',
                  borderRadius: radius.md,
                  borderColor: on ? color.copper : color.benchLine,
                  background: on ? color.copperFaint : color.benchRaised,
                }}
              >
                {o.caution && (
                  <span title={o.caution} style={{ position: 'absolute', top: 3, left: 3, display: 'flex' }}>
                    <TriangleAlert size={10} strokeWidth={2.25} aria-label={o.caution} style={{ color: color.statusCaution }} />
                  </span>
                )}
                <span style={{ fontWeight: type.weight.semi, color: on ? color.copperBright : color.inkPrimary }}>{o.label}</span>
                <span style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>{o.hint}</span>
              </button>
            )
          })}
        </div>
        {path === 'key' ? (
          <>
            <div style={{ fontSize: type.size.sm, color: color.inkSecondary }}>
              Paste a key from any one of these. Keys are encrypted on this computer.
            </div>
            <ProviderCards statuses={state.statuses} onChanged={state.reload} />
          </>
        ) : (
          <SubscriptionPanel key={path} id={path} state={state} />
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={onDone}
            style={connected ? { borderColor: color.copper, color: color.copperBright } : undefined}
          >
            {connected ? 'Continue' : 'Skip for now'}
          </button>
          {!connected && (
            <span style={{ fontSize: type.size.sm, color: color.inkMuted }}>
              Free checks and the BOM still work without a model.
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

const SUB_COPY: Record<EngineId, string> = {
  codex:
    'The app runs your installed Codex CLI, signed in with your ChatGPT account. Your login stays with the CLI. ' +
    'Reviews use your plan’s usage limit; datasheets are read as extracted text.',
  claude:
    'The app runs your installed Claude Code, signed in with your Claude Pro or Max account. Your login stays ' +
    'with the CLI. Replies and reviews use your plan’s usage limit.',
}

// Install → (Claude: accept the terms risk) → sign in → use: each step only once
// the one before it checks out.
function SubscriptionPanel({ id, state }: { id: EngineId; state: ModelState }) {
  const engines = useEngines(state)
  const st = engines.status[id]
  const cfg = engines.settings[id] ?? {}
  const on = !!cfg.enabled
  const tosAck = id === 'claude' ? !!engines.settings.claude?.tosAck : true
  const vendor = id === 'claude' ? 'claude' : 'openai'
  const [msg, setMsg] = useState<string | null>(null)

  async function login() {
    setMsg(null)
    try {
      await startEngineLogin(id)
      setMsg('Finish signing in in the terminal window, then press Check again.')
    } catch (e) {
      setMsg(ipcMessage(e))
    }
  }

  return (
    <div
      style={{
        border: `1px solid ${color.benchLine}`,
        borderRadius: radius.md,
        background: color.benchRaised,
        padding: '12px 14px',
        display: 'grid',
        gap: 10,
      }}
    >
      <div style={{ fontSize: type.size.sm, color: color.inkSecondary, lineHeight: type.line.sm }}>{SUB_COPY[id]}</div>
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
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={tosAck}
              disabled={engines.busy}
              onChange={(e) =>
                engines.save({ claude: { ...cfg, tosAck: e.target.checked, enabled: e.target.checked ? cfg.enabled : false } })
              }
              style={{ accentColor: color.copper }}
            />
            I understand and accept this risk
          </label>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {st?.installed && !st.logged_in && (
          <button onClick={login} disabled={engines.busy}>
            Sign in…
          </button>
        )}
        <button onClick={engines.recheck} disabled={engines.busy}>
          {engines.busy ? 'Checking…' : 'Check again'}
        </button>
        <span style={{ flex: 1 }} />
        <button
          disabled={engines.busy || !st?.logged_in || on || !tosAck}
          title={!tosAck ? 'Accept the terms risk above first' : undefined}
          onClick={() => engines.save({ [id]: { ...cfg, enabled: true }, preferredRoute: { [vendor]: 'sub' } })}
          style={st?.logged_in && !on && tosAck ? { borderColor: color.copper, color: color.copperBright } : undefined}
        >
          {on ? 'Connected' : 'Use my subscription'}
        </button>
      </div>
      {msg && <div style={{ fontSize: type.size.sm, color: color.inkSecondary }}>{msg}</div>}
      {engines.error && <div style={{ fontSize: type.size.sm, color: color.statusCritical }}>{engines.error}</div>}
    </div>
  )
}
