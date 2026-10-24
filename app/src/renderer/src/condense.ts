// When a long chat is condensed into a summary plus its latest turns (Settings >
// General). Per-machine preference, so it lives in localStorage; the sidecar gets
// it with each message (agent/condense.py). It never goes past half a model's
// context window, so a small local model condenses sooner.
export type CondenseLevel = 'off' | 'normal' | 'aggressive' | 'custom'

export const CONDENSE_LEVELS: { id: CondenseLevel; label: string; hint: string }[] = [
  { id: 'off', label: 'Off', hint: 'Never condense. Every message resends the whole chat, so long chats get expensive.' },
  {
    id: 'normal',
    label: 'Normal',
    hint: 'Condense once a request carries about 40k tokens. The default.',
  },
  {
    id: 'aggressive',
    label: 'Aggressive',
    hint: 'Condense at about 20k tokens. Cheapest; the agent re-reads datasheets more often.',
  },
  { id: 'custom', label: 'Custom', hint: 'Condense at the size you pick.' },
]

export const CUSTOM_MIN = 10_000
export const CUSTOM_MAX = 150_000
export const CUSTOM_STEP = 5_000

const KEY = 'pcb.condense'
const CUSTOM_KEY = 'pcb.condenseAt'

export function getCondense(): CondenseLevel {
  try {
    const v = localStorage.getItem(KEY)
    if (CONDENSE_LEVELS.some((l) => l.id === v)) return v as CondenseLevel
  } catch {
    // storage unavailable — fall through to the default
  }
  return 'normal'
}

export function getCondenseAt(): number {
  try {
    const v = Number(localStorage.getItem(CUSTOM_KEY))
    if (v >= CUSTOM_MIN && v <= CUSTOM_MAX) return v
  } catch {
    // storage unavailable — fall through to the default
  }
  return 60_000
}

export function setCondense(level: CondenseLevel, at?: number): void {
  try {
    localStorage.setItem(KEY, level)
    if (at !== undefined) localStorage.setItem(CUSTOM_KEY, String(at))
  } catch {
    // not persisted; this session still uses the default
  }
}
