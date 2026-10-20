// How hard the chat model reasons before it answers (the composer's effort chip).
// Per-machine preference, so it lives in localStorage; the sidecar gets it with
// each message and maps it onto each provider's own scale (Max clamps to the
// highest level a model has).
export type Effort = 'low' | 'medium' | 'high' | 'max'

export const EFFORTS: { id: Effort; label: string; hint: string }[] = [
  { id: 'low', label: 'Low', hint: 'Quick answers and lookups. Thinks least; fastest and cheapest.' },
  { id: 'medium', label: 'Medium', hint: 'Some reasoning before answering. A good everyday pace.' },
  { id: 'high', label: 'High', hint: 'Careful reasoning for most design questions. The default.' },
  { id: 'max', label: 'Max', hint: 'Thinks longest before answering. Slowest and heaviest on tokens.' },
]

const KEY = 'pcb.chatEffort'
const DEFAULT: Effort = 'high'

export function getEffort(): Effort {
  try {
    const v = localStorage.getItem(KEY)
    if (EFFORTS.some((e) => e.id === v)) return v as Effort
  } catch {
    // storage unavailable — fall through to the default
  }
  return DEFAULT
}

export function setEffort(effort: Effort): void {
  try {
    localStorage.setItem(KEY, effort)
  } catch {
    // not persisted; this session still sends what was picked
  }
}
