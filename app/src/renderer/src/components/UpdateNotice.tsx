import { useEffect, useState } from 'react'
import type { UpdateStatus } from '../global'
import { color, radius, type } from '../theme'

// The main process's update state (src/main/updater.ts), kept live.
export function useUpdateStatus(): UpdateStatus | null {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  useEffect(() => {
    let alive = true
    window.pcb
      .updateStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => {})
    const off = window.pcb.onUpdateStatus(setStatus)
    return () => {
      alive = false
      off()
    }
  }, [])
  return status
}

// In the header corner, only while there's something to say: a download in
// progress, a downloaded update to restart into, or a newer release to fetch.
export function UpdatePill() {
  const s = useUpdateStatus()
  if (!s || !s.version) return null
  const pill = {
    fontSize: type.size.xs,
    padding: '3px 9px',
    borderRadius: radius.lg,
    whiteSpace: 'nowrap' as const,
  }
  if (s.state === 'downloading') {
    return (
      <span style={{ ...pill, color: color.inkMuted, border: `1px solid ${color.benchLine}` }}>
        Downloading Ohmen {s.version}… {s.percent ?? 0}%
      </span>
    )
  }
  if (s.state === 'ready' || s.state === 'available') {
    return (
      <button
        onClick={() => window.pcb.installUpdate()}
        title={s.state === 'ready' ? 'Ohmen closes, updates and opens again' : 'Opens the release page to download it'}
        style={{ ...pill, borderColor: color.copper, color: color.copperBright, background: color.copperFaint }}
      >
        {s.state === 'ready' ? `Restart to update to ${s.version}` : `Ohmen ${s.version} is out — download`}
      </button>
    )
  }
  return null
}

// Settings › About: what's installed, the last check, and the controls.
export function UpdateSettings() {
  const s = useUpdateStatus()
  const [auto, setAuto] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    window.pcb
      .getSettings()
      .then((st) => setAuto(st.updateChecks !== false))
      .catch(() => {})
  }, [])

  async function check() {
    setBusy(true)
    try {
      await window.pcb.checkForUpdates()
    } finally {
      setBusy(false)
    }
  }

  const line = !s
    ? ''
    : s.state === 'checking'
      ? 'Checking for updates…'
      : s.state === 'none'
        ? `You're on the latest version.`
        : s.state === 'downloading'
          ? `Downloading ${s.version}… ${s.percent ?? 0}%`
          : s.state === 'ready'
            ? `${s.version} is downloaded. It installs when you restart Ohmen.`
            : s.state === 'available'
              ? `${s.version} is available.${s.canInstall ? '' : ' This install type updates through its download.'}`
              : s.state === 'error'
                ? `Couldn't check for updates: ${s.message ?? 'unknown error'}`
                : 'Not checked yet.'

  return (
    <div style={{ display: 'grid', gap: 6, marginTop: 6 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: s?.state === 'error' ? color.statusCaution : color.inkSecondary }}>{line}</span>
        <span style={{ flex: 1 }} />
        {(s?.state === 'ready' || s?.state === 'available') && (
          <button onClick={() => window.pcb.installUpdate()} style={{ borderColor: color.copper, color: color.copperBright }}>
            {s.state === 'ready' ? 'Restart to update' : 'Download'}
          </button>
        )}
        <button onClick={check} disabled={busy || s?.state === 'checking' || s?.state === 'downloading'}>
          {busy ? 'Checking…' : 'Check now'}
        </button>
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => {
            const v = e.target.checked
            setAuto(v)
            window.pcb.setSettings({ updateChecks: v }).catch(() => {})
          }}
          style={{ accentColor: color.copper }}
        />
        Check for updates automatically
      </label>
    </div>
  )
}
