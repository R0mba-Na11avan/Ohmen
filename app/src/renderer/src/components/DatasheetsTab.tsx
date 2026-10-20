import { useEffect, useState, type DragEvent } from 'react'
import {
  datasheetUrl,
  fetchDatasheets,
  getDatasheets,
  uploadDatasheet,
  type DatasheetEntry,
} from '../api'
import { color, radius, space, type } from '../theme'

const SOURCE_LABEL: Record<string, string> = {
  schematic: 'schematic field',
  manufacturer: 'manufacturer site',
  digikey: 'DigiKey',
  upload: 'added by you',
}

// The project's datasheet library: one row per part that has a datasheet. The
// files live in <project>/datasheets/; the agent reads them page by page and
// cites them. A part nothing could find takes a dropped-in PDF.
export function DatasheetsTab({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<DatasheetEntry[]>([])
  const [busy, setBusy] = useState<string | null>(null) // key being fetched, or '*' for all
  const [error, setError] = useState('')
  const [dragKey, setDragKey] = useState<string | null>(null)

  useEffect(() => {
    getDatasheets().then(setRows).catch(() => setRows([]))
  }, [refreshKey])

  async function fetchAll(key?: string, refresh = false) {
    setError('')
    setBusy(key ?? '*')
    try {
      setRows(await fetchDatasheets(key, refresh))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function upload(key: string, file: File | undefined) {
    if (!file) return
    setError('')
    if (file.type && file.type !== 'application/pdf') {
      setError(`${file.name} isn't a PDF`)
      return
    }
    setBusy(key)
    try {
      await uploadDatasheet(key, file)
      setRows(await getDatasheets())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function open(key: string) {
    window.open(await datasheetUrl(key), '_blank')
  }

  const have = rows.filter((r) => r.status === 'ok').length
  const missing = rows.length - have

  if (rows.length === 0) {
    return (
      <div style={{ color: color.inkSecondary, fontSize: type.size.base, lineHeight: type.line.base }}>
        No parts with datasheets yet. Sync a project with ICs, connectors or other parts that carry a part number.
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: space[4] }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: space[3], flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: type.size.lg, fontWeight: type.weight.semi }}>Datasheets</div>
          <div style={{ fontSize: type.size.sm, color: color.inkSecondary, marginTop: 2 }}>
            {have} of {rows.length} stored in <span style={{ fontFamily: type.mono }}>datasheets/</span>
            {missing > 0 && ' · drop a PDF on a row to add one yourself'}
          </div>
        </div>
        <span style={{ flex: 1 }} />
        <button onClick={() => fetchAll()} disabled={busy !== null || missing === 0}>
          {busy === '*' ? 'Fetching…' : missing ? `Fetch ${missing} missing` : 'All fetched'}
        </button>
      </div>

      {error && <div style={{ color: color.statusCritical, fontSize: type.size.sm }}>{error}</div>}

      <div style={{ border: `1px solid ${color.benchLine}`, borderRadius: radius.md, overflow: 'hidden' }}>
        {rows.map((r, i) => {
          const ok = r.status === 'ok'
          const dragging = dragKey === r.key
          const working = busy === r.key || (busy === '*' && !ok)
          const onDrop = (e: DragEvent) => {
            e.preventDefault()
            setDragKey(null)
            upload(r.key, e.dataTransfer.files[0])
          }
          return (
            <div
              key={r.key}
              onDragOver={(e) => {
                e.preventDefault()
                setDragKey(r.key)
              }}
              onDragLeave={() => setDragKey(null)}
              onDrop={onDrop}
              style={{
                display: 'grid',
                gridTemplateColumns: '14px minmax(0, 1fr) auto',
                gap: space[3],
                alignItems: 'start',
                padding: `${space[3]}px ${space[4]}px`,
                borderTop: i ? `1px solid ${color.benchLine}` : undefined,
                background: dragging ? color.copperFaint : color.benchRaised,
                outline: dragging ? `1px dashed ${color.copper}` : undefined,
                outlineOffset: -3,
              }}
            >
              <StatusMark status={r.status} working={working} />
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: space[2], flexWrap: 'wrap' }}>
                  <span style={{ fontFamily: type.mono, fontWeight: type.weight.med }}>{r.key}</span>
                  <span style={{ fontFamily: type.mono, fontSize: type.size.sm, color: color.inkMuted }}>
                    {r.refs.join(', ')}
                  </span>
                </div>
                <div style={{ fontSize: type.size.sm, color: color.inkSecondary, marginTop: 2 }}>
                  {working
                    ? 'Looking for the datasheet…'
                    : ok
                      ? `${r.pages} pages · ${SOURCE_LABEL[r.source ?? ''] ?? r.source}`
                      : r.status === 'mismatch'
                        ? 'Only found the wrong document — drop the right PDF here'
                        : r.tried.length
                          ? 'Not found — drop the PDF here'
                          : 'Not fetched yet'}
                </div>
                {!ok && !working && r.tried.length > 0 && (
                  <details style={{ marginTop: 4 }}>
                    <summary style={{ fontSize: type.size.xs, color: color.inkMuted, cursor: 'pointer' }}>
                      what was tried ({r.tried.length})
                    </summary>
                    <div style={{ display: 'grid', gap: 2, marginTop: 4 }}>
                      {r.tried.map((a, j) => (
                        <div
                          key={j}
                          title={a.url ?? undefined}
                          style={{
                            fontFamily: type.mono,
                            fontSize: type.size.xs,
                            color: color.inkMuted,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {a.source}: {a.error}
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
              <div style={{ display: 'flex', gap: space[2], alignItems: 'center' }}>
                {ok && <button onClick={() => open(r.key)}>Open</button>}
                <button onClick={() => fetchAll(r.key, ok)} disabled={busy !== null} title={ok ? 'Download again' : 'Try again'}>
                  {ok ? 'Refetch' : 'Retry'}
                </button>
                <label
                  style={{
                    fontSize: type.size.sm,
                    color: color.copper,
                    cursor: busy ? 'default' : 'pointer',
                    padding: '4px 2px',
                  }}
                >
                  {ok ? 'Replace' : 'Add PDF'}
                  <input
                    type="file"
                    accept="application/pdf"
                    disabled={busy !== null}
                    onChange={(e) => {
                      upload(r.key, e.target.files?.[0])
                      e.target.value = ''
                    }}
                    style={{ display: 'none' }}
                  />
                </label>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StatusMark({ status, working }: { status: DatasheetEntry['status']; working: boolean }) {
  if (working) {
    return (
      <span className="breathe" style={{ color: color.agent, lineHeight: '20px' }}>
        ●
      </span>
    )
  }
  const ok = status === 'ok'
  return (
    <span
      aria-label={ok ? 'stored' : status}
      style={{
        color: ok ? color.statusVerified : status === 'mismatch' ? color.statusCaution : color.inkMuted,
        lineHeight: '20px',
        fontFamily: type.mono,
      }}
    >
      {ok ? '✓' : status === 'mismatch' ? '!' : '○'}
    </span>
  )
}
