import { useState } from 'react'
import { answerPrompt, type ChatPrompt, type PromptAnswer, type PromptQuestion } from '../api'
import { color, radius, type } from '../theme'

// Claude Code asking mid-turn, answered here: may it use a tool (Allow / Deny),
// or which option you pick (its AskUserQuestion). The turn waits on the card.
// `outcome` arrives from the sidecar once it's settled (answered, timed out,
// or the turn stopped); `live` is false once the turn is over.
export function PromptCard({ prompt, outcome, live }: { prompt: ChatPrompt; outcome?: string; live: boolean }) {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  async function send(answer: PromptAnswer) {
    setSending(true)
    setError('')
    try {
      await answerPrompt(prompt.id, answer)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSending(false)
    }
  }

  const settled = outcome !== undefined || !live
  if (settled) {
    return (
      <div style={{ marginTop: 8, fontFamily: type.mono, fontSize: type.size.xs, color: color.inkSecondary }}>
        <span style={{ color: color.agent }}>{prompt.kind === 'question' ? '? ' : '⚑ '}</span>
        {prompt.kind === 'question' ? prompt.questions.map((q) => q.question).join(' / ') : toolSummary(prompt.tool, prompt.input)}
        {' — '}
        {outcome ?? 'no longer waiting'}
      </div>
    )
  }

  return (
    <div
      role="group"
      aria-label={prompt.kind === 'question' ? 'Claude Code is asking you' : 'Claude Code needs permission'}
      style={{
        marginTop: 8,
        border: `1px solid ${color.agentDim}`,
        background: color.agentFaint,
        borderRadius: radius.md,
        padding: '10px 12px',
        display: 'grid',
        gap: 8,
      }}
    >
      {prompt.kind === 'permission' ? (
        <PermissionBody prompt={prompt} busy={sending} onAnswer={send} />
      ) : (
        <QuestionBody questions={prompt.questions} busy={sending} onAnswer={send} />
      )}
      {error && <div style={{ color: color.statusCritical, fontSize: type.size.xs }}>{error}</div>}
    </div>
  )
}

function PermissionBody({
  prompt,
  busy,
  onAnswer,
}: {
  prompt: Extract<ChatPrompt, { kind: 'permission' }>
  busy: boolean
  onAnswer: (a: PromptAnswer) => void
}) {
  const detail = toolDetail(prompt.tool, prompt.input)
  return (
    <>
      <div style={{ fontSize: type.size.sm, color: color.inkPrimary }}>
        Claude Code wants to use <span style={{ fontFamily: type.mono, color: color.agent }}>{prompt.tool}</span>
      </div>
      {detail && (
        <pre
          style={{
            margin: 0,
            fontFamily: type.mono,
            fontSize: type.size.xs,
            lineHeight: type.line.xs,
            color: color.inkSecondary,
            background: color.benchBase,
            border: `1px solid ${color.benchLine}`,
            borderRadius: radius.sm,
            padding: '6px 8px',
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
            maxHeight: 180,
            overflow: 'auto',
          }}
        >
          {detail}
        </pre>
      )}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <button
          onClick={() => onAnswer({ allow: true })}
          disabled={busy}
          style={{ borderColor: color.copper, color: color.copperBright }}
        >
          Allow
        </button>
        <button onClick={() => onAnswer({ allow: true, always: true })} disabled={busy} title="Until Ohmen restarts">
          Allow {prompt.tool} for this chat
        </button>
        <button onClick={() => onAnswer({ allow: false })} disabled={busy}>
          Deny
        </button>
      </div>
    </>
  )
}

const OTHER = '\u0000other'

function QuestionBody({
  questions,
  busy,
  onAnswer,
}: {
  questions: PromptQuestion[]
  busy: boolean
  onAnswer: (a: PromptAnswer) => void
}) {
  // per question: the picked labels (OTHER = the typed answer)
  const [picked, setPicked] = useState<string[][]>(() => questions.map(() => []))
  const [other, setOther] = useState<string[]>(() => questions.map(() => ''))
  // one single-choice question: a click answers it
  const oneClick = questions.length === 1 && !questions[0].multiSelect

  const answerOf = (i: number, labels = picked[i]) =>
    labels
      .map((l) => (l === OTHER ? other[i].trim() : l))
      .filter(Boolean)
      .join(', ')

  function submit(next = picked) {
    const answers: Record<string, string> = {}
    questions.forEach((q, i) => {
      answers[q.question] = answerOf(i, next[i])
    })
    onAnswer({ answers })
  }

  function choose(i: number, label: string) {
    const q = questions[i]
    const cur = picked[i]
    const nextFor = q.multiSelect ? (cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label]) : [label]
    const next = picked.map((p, j) => (j === i ? nextFor : p))
    setPicked(next)
    if (oneClick && label !== OTHER) submit(next)
  }

  const complete = questions.every((_, i) => answerOf(i) !== '')

  return (
    <>
      {questions.map((q, i) => (
        <div key={i} style={{ display: 'grid', gap: 6 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
            {q.header && (
              <span
                style={{
                  fontFamily: type.mono,
                  fontSize: type.size.xs,
                  color: color.agent,
                  border: `1px solid ${color.agentDim}`,
                  borderRadius: radius.sm,
                  padding: '0 5px',
                }}
              >
                {q.header}
              </span>
            )}
            <span style={{ fontSize: type.size.sm, color: color.inkPrimary }}>{q.question}</span>
            {q.multiSelect && <span style={{ fontSize: type.size.xs, color: color.inkMuted }}>pick any</span>}
          </div>
          <div style={{ display: 'grid', gap: 4 }}>
            {q.options.map((o) => {
              const on = picked[i].includes(o.label)
              return (
                <button
                  key={o.label}
                  onClick={() => choose(i, o.label)}
                  disabled={busy}
                  aria-pressed={on}
                  style={{
                    textAlign: 'left',
                    display: 'grid',
                    gap: 1,
                    padding: '6px 9px',
                    background: on ? color.copperFaint : color.benchRaised,
                    borderColor: on ? color.copper : color.benchLineStrong,
                  }}
                >
                  <span style={{ fontSize: type.size.sm, color: on ? color.copperBright : color.inkPrimary }}>{o.label}</span>
                  {o.description && (
                    <span style={{ fontSize: type.size.xs, color: color.inkSecondary }}>{o.description}</span>
                  )}
                </button>
              )
            })}
            <input
              value={other[i]}
              onChange={(e) => {
                const v = e.target.value
                setOther((os) => os.map((x, j) => (j === i ? v : x)))
                setPicked((ps) =>
                  ps.map((p, j) =>
                    j !== i
                      ? p
                      : v.trim()
                        ? q.multiSelect
                          ? p.includes(OTHER)
                            ? p
                            : [...p, OTHER]
                          : [OTHER]
                        : p.filter((l) => l !== OTHER),
                  ),
                )
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && complete && !busy) submit()
              }}
              disabled={busy}
              placeholder="Or type your own answer"
              aria-label={`Your own answer to: ${q.question}`}
              style={{ fontSize: type.size.sm }}
            />
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 6 }}>
        {(!oneClick || other[0].trim()) && (
          <button
            onClick={() => submit()}
            disabled={busy || !complete}
            style={{ borderColor: color.copper, color: color.copperBright }}
          >
            Send answer
          </button>
        )}
        <button onClick={() => onAnswer({ message: 'The user skipped the question.' })} disabled={busy}>
          Skip
        </button>
      </div>
    </>
  )
}

// One line for a settled card: "Read notes.txt", "Bash: git status".
function toolSummary(tool: string, input: Record<string, unknown>): string {
  const s = (v: unknown) => String(v ?? '').replace(/\s+/g, ' ').slice(0, 80)
  if (typeof input.file_path === 'string') return `${tool} ${input.file_path.split(/[\\/]/).pop()}`
  if (typeof input.command === 'string') return `${tool}: ${s(input.command)}`
  if (typeof input.pattern === 'string') return `${tool} ${s(input.pattern)}`
  if (typeof input.url === 'string') return `${tool} ${s(input.url)}`
  return tool
}

// What exactly it wants to do, for the card body.
function toolDetail(tool: string, input: Record<string, unknown>): string {
  const clip = (v: unknown, n = 1200) => {
    const t = String(v ?? '')
    return t.length > n ? t.slice(0, n) + '\n…' : t
  }
  if (typeof input.command === 'string') return clip(input.command)
  if (tool === 'Edit' && typeof input.file_path === 'string') {
    return `${input.file_path}\n\n- ${clip(input.old_string, 500)}\n+ ${clip(input.new_string, 500)}`
  }
  if (tool === 'Write' && typeof input.file_path === 'string') return `${input.file_path}\n\n${clip(input.content, 600)}`
  if (typeof input.file_path === 'string') return input.file_path
  if (typeof input.url === 'string') return input.url
  const json = JSON.stringify(input, null, 2)
  return json === '{}' ? '' : clip(json)
}
