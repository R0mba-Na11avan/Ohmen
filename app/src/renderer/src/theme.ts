// One source of truth for the "Bench & Datasheet" identity. Inline styles read
// this object; global.css mirrors the same values as CSS custom properties for
// the things inline can't do (focus ring, keyframes, dot-grid, ::selection).
//
// Meaning, not mood: cool teal-charcoal = the tool's bench; warm paper = external
// datasheet proof; copper = you / your action; purple = the agent's voice; the
// status hues appear ONLY on severity + state.

export const color = {
  // Bench — cool teal-charcoal (the green cast is deliberate, never neutral #111)
  benchVoid: '#0A0F0D', // app background / empty states
  benchBase: '#0E1512', // primary workspace surface
  benchRaised: '#141D19', // panels, feed items, modules
  benchLine: '#26332D', // hairline dividers & borders
  benchLineStrong: '#35453D', // active panel edge

  inkPrimary: '#E4EAE6',
  inkSecondary: '#9FB0A8',
  inkMuted: '#6B7C74',

  // Copper = you / interactive / primary action / normal signal
  copper: '#D68A4C',
  copperBright: '#EEA766',
  copperDim: '#8A5E34',
  copperFaint: 'rgba(214,138,76,0.12)',

  // Agent purple = the AI's voice
  agent: '#A374CE',
  agentDim: '#6E4E92',
  agentFaint: 'rgba(163,116,206,0.12)',

  // Status = severity & state ONLY
  statusCritical: '#E5484D', // must-fix (board-killer)
  statusCaution: '#E0912F', // verify (needs a human call)
  statusVerified: '#46C88A', // clean / grounded & confirmed
  statusCriticalFaint: 'rgba(229,72,77,0.10)',
  statusCautionFaint: 'rgba(224,145,47,0.10)',

  // Datasheet paper = evidence material
  paper: '#EDE6D6',
  paperEdge: '#D8CDB6',
  paperInk: '#211E17',
  paperInkSoft: '#5A5346',
  paperRule: '#C4B89D',
  paperMark: 'rgba(214,138,76,0.30)',
} as const

export const type = {
  sans: '"IBM Plex Sans", system-ui, sans-serif',
  mono: '"IBM Plex Mono", ui-monospace, monospace',
  size: { xs: '11px', sm: '12.5px', base: '14px', md: '16px', lg: '19px', xl: '24px', xxl: '30px' },
  line: { xs: '16px', sm: '18px', base: '21px', md: '24px', lg: '26px', xl: '30px' },
  weight: { reg: 400, med: 500, semi: 600 },
} as const

export const radius = { sm: '4px', md: '6px', lg: '10px' } as const

// One spacing scale — tight groups, generous separation between them.
export const space = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 7: 32, 8: 48 } as const

// Reading measure caps: text tabs stay legible (~70ch); tables/diagram run wide.
export const measure = { text: 760, wide: 1100 } as const

// Severity vocab bridge: our brain emits must-fix/verify/cosmetic; the visual
// legend is critical/caution/ink. One place maps the two.
export const severity = {
  'must-fix': { tone: color.statusCritical, faint: color.statusCriticalFaint, mark: 'square', label: 'must-fix' },
  verify: { tone: color.statusCaution, faint: color.statusCautionFaint, mark: 'triangle', label: 'verify' },
  cosmetic: { tone: color.inkSecondary, faint: 'transparent', mark: 'dot', label: 'cosmetic' },
} as const

export type SeverityKey = keyof typeof severity
