import { useState } from 'react'
import { FilePlus2, FolderOpen } from 'lucide-react'
import { sync, type DesignSummary } from '../api'
import { PipesBackdrop } from './PipesBackdrop'
import { color, space, type } from '../theme'

// The empty start screen: open an existing KiCad project or create a new one.
export function StartScreen({ onLoaded }: { onLoaded: (s: DesignSummary) => void }) {
  const [busy, setBusy] = useState<'open' | 'new' | null>(null)
  const [err, setErr] = useState('')

  async function load(kind: 'open' | 'new') {
    setErr('')
    try {
      const path = kind === 'open' ? await window.pcb.pickProject() : await window.pcb.newProject()
      if (!path) return
      setBusy(kind)
      onLoaded(await sync(path))
    } catch (e) {
      // ipcRenderer.invoke wraps main-process errors; keep only the message
      setErr(String(e).replace(/^Error: Error invoking remote method '[^']+': (Error: )?/, ''))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div
      className="start-in"
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: space[3],
        textAlign: 'center',
        padding: space[6],
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <PipesBackdrop />
      <div
        style={{
          position: 'absolute',
          top: 14,
          left: 16,
          fontWeight: type.weight.semi,
          letterSpacing: '-0.01em',
          color: color.copper,
        }}
      >
        Ohmen
      </div>
      <div style={{ position: 'relative', fontSize: type.size.xl, fontWeight: type.weight.semi, letterSpacing: '-0.02em' }}>
        Open or create a KiCad project
      </div>
      <div style={{ position: 'relative', fontSize: type.size.base, color: color.inkSecondary, maxWidth: 460, lineHeight: type.line.base }}>
        Point the copilot at your project folder. It reads the schematic — never edits it — then
        reviews, researches parts, and builds the BOM alongside you.
      </div>
      <div style={{ position: 'relative', display: 'flex', gap: space[3], marginTop: space[3] }}>
        <button
          onClick={() => load('open')}
          disabled={!!busy}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '9px 16px',
            background: color.copper,
            borderColor: color.copper,
            color: color.benchVoid,
            fontWeight: type.weight.med,
          }}
        >
          <FolderOpen size={15} strokeWidth={1.75} />
          {busy === 'open' ? 'Loading…' : 'Open project…'}
        </button>
        <button
          onClick={() => load('new')}
          disabled={!!busy}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 16px', background: color.benchRaised }}
        >
          <FilePlus2 size={15} strokeWidth={1.75} />
          {busy === 'new' ? 'Creating…' : 'New project…'}
        </button>
      </div>
      {err && (
        <div style={{ position: 'relative', color: color.statusCritical, fontSize: type.size.sm, maxWidth: 520 }}>{err}</div>
      )}
    </div>
  )
}
