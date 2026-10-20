import { useState, type ReactNode } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { type DesignSummary } from '../api'
import { color, type } from '../theme'

// The project as people call it: its .kicad_pro (or schematic) name, no extension.
export function projectName(summary: DesignSummary): string {
  const file = (summary.project_file ?? summary.source).split(/[\\/]/).pop() ?? ''
  return file.replace(/\.(kicad_pro|kicad_sch|net|xml|json)$/i, '')
}

// The instrument header — source readout, shown once a project is open.
// Opening / creating a project lives on the start screen; `onSwitch` goes back
// to it. Clicking the project name opens its folder or KiCad (a setting).
// `right` holds the app-wide controls (settings).
export function SyncBar({
  summary,
  onSwitch,
  right,
}: {
  summary: DesignSummary
  onSwitch: () => void
  right?: ReactNode
}) {
  const [note, setNote] = useState('')
  // Named by its .kicad_pro so it reads as a KiCad project, not one schematic.
  const name = (summary.project_file ?? summary.source).split(/[\\/]/).pop()

  async function reveal() {
    try {
      setNote(await window.pcb.revealProject(summary.source))
    } catch (e) {
      // ipcRenderer.invoke wraps main-process errors; keep only the message
      setNote(String(e).replace(/^Error: Error invoking remote method '[^']+': (Error: )?/, ''))
    }
  }

  return (
    <div
      className="bar-in"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        padding: '10px 16px',
        borderBottom: `1px solid ${color.benchLine}`,
        background: color.benchBase,
      }}
    >
      <span style={{ fontWeight: type.weight.semi, letterSpacing: '-0.01em', color: color.copper }}>
        Ohmen
      </span>
      <button onClick={onSwitch}>Switch project…</button>
      <span style={{ fontSize: type.size.sm, color: color.inkSecondary, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button
          className="project-name"
          onClick={reveal}
          title="Open the project (folder or KiCad — Settings › General)"
          style={{ fontSize: type.size.sm }}
        >
          {name}
          <ArrowUpRight className="project-name-icon" size={13} strokeWidth={1.75} aria-hidden />
        </button>
        <span style={{ color: color.inkMuted }}>·</span>
        <span>
          <span style={{ fontFamily: type.mono, color: color.inkPrimary }}>{summary.components}</span> components
        </span>
        <span style={{ color: color.inkMuted }}>·</span>
        <span>
          <span style={{ fontFamily: type.mono, color: color.inkPrimary }}>{summary.nets}</span> nets
        </span>
      </span>
      {note && (
        <span
          onClick={() => setNote('')}
          title="dismiss"
          style={{ fontSize: type.size.sm, color: color.statusCaution, cursor: 'pointer' }}
        >
          {note}
        </span>
      )}
      <span style={{ flex: 1 }} />
      {right}
    </div>
  )
}
