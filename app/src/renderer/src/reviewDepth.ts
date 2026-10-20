// How many reviewers a design review uses (Settings > General). Per-machine
// preference, so it lives in localStorage; the sidecar gets it with each request.
export type ReviewDepth = 'low' | 'medium' | 'high'

export const REVIEW_DEPTHS: { id: ReviewDepth; label: string; hint: string }[] = [
  {
    id: 'low',
    label: 'Low',
    hint: 'One reviewer audits every part against its datasheet, one after another. Slowest; fewest tokens at once.',
  },
  {
    id: 'medium',
    label: 'Medium',
    hint: 'A power reviewer and a signals reviewer audit their parts in parallel; the copilot writes it up.',
  },
  {
    id: 'high',
    label: 'High',
    hint: 'The copilot maps the board into functional blocks (up to 6), one reviewer each, all in parallel. Fastest; heaviest on rate limits.',
  },
]

const KEY = 'pcb.reviewDepth'

export function getReviewDepth(): ReviewDepth {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'low' || v === 'medium' || v === 'high') return v
  } catch {
    // storage unavailable — fall through to the default
  }
  return 'low'
}

export function setReviewDepth(depth: ReviewDepth): void {
  try {
    localStorage.setItem(KEY, depth)
  } catch {
    // not persisted; this session still uses the default
  }
}
