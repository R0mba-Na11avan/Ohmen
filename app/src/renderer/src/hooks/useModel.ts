import { useCallback, useEffect, useState } from 'react'
import { getModels, getProviderStatus, isSubSpec, type ModelInfo, type ProviderId, type ProviderStatus } from '../api'

// The one place the active model lives. Every surface that picks or reads the
// model (top-bar picker, chat, review, diagram) goes through this, so the picker
// can move without touching the rest.
export interface ModelState {
  models: ModelInfo[]
  model: string // '' = nothing usable connected
  info: ModelInfo | null
  statuses: Partial<Record<ProviderId, ProviderStatus>>
  anyConfigured: boolean
  ready: boolean // first load done (saved keys pushed + models listed)
  recent: string[] // most recent first, includes the active model
  starred: string[]
  setModel: (spec: string) => void
  toggleStar: (spec: string) => void
  reload: () => Promise<void>
}

const RECENT_MAX = 5

// Keep the user's pick if it still exists — on whichever route the vendor's
// preferred-route setting now gives that model; else the first recommended; else anything.
function choose(models: ModelInfo[], wanted: string | undefined): string {
  const row = wanted ? models.find((m) => m.spec === wanted || Object.values(m.routes ?? {}).includes(wanted)) : undefined
  if (row) return row.spec
  return (models.find((m) => m.recommended) ?? models[0])?.spec ?? ''
}

export function useModel(): ModelState {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [model, setModelState] = useState('')
  const [statuses, setStatuses] = useState<Partial<Record<ProviderId, ProviderStatus>>>({})
  const [ready, setReady] = useState(false)
  const [recent, setRecent] = useState<string[]>([])
  const [starred, setStarred] = useState<string[]>([])

  const reload = useCallback(async () => {
    try {
      // Main pushes the saved keys at launch; wait so "no models" isn't premature.
      await window.pcb.sidecarReady()
      // A request can still fail just after the sidecar comes up (seen on
      // Windows as ERR_NO_BUFFER_SPACE); one blip mustn't send a returning
      // user to "Connect a model", so try a couple more times first.
      const load = () => Promise.all([getModels(), getProviderStatus(), window.pcb.getSettings()])
      let tries = 0
      const attempt = async (): Promise<Awaited<ReturnType<typeof load>>> => {
        try {
          return await load()
        } catch (e) {
          if (++tries >= 3) throw e
          await new Promise((r) => setTimeout(r, 800 * tries))
          return attempt()
        }
      }
      const [ms, st, settings] = await attempt()
      setModels(ms)
      setStatuses(st)
      setRecent(settings.recentModels ?? [])
      setStarred(settings.starredModels ?? [])
      setModelState((current) => choose(ms, current || settings.selectedModel))
    } catch (e) {
      console.error('model list failed', e)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const setModel = useCallback((spec: string) => {
    setModelState(spec)
    setRecent((prev) => {
      const next = [spec, ...prev.filter((s) => s !== spec)].slice(0, RECENT_MAX)
      window.pcb.setSettings({ selectedModel: spec, recentModels: next }).catch(() => {})
      return next
    })
  }, [])

  const toggleStar = useCallback((spec: string) => {
    setStarred((prev) => {
      const next = prev.includes(spec) ? prev.filter((s) => s !== spec) : [...prev, spec]
      window.pcb.setSettings({ starredModels: next }).catch(() => {})
      return next
    })
  }, [])

  return {
    models,
    model,
    info: models.find((m) => m.spec === model) ?? null,
    statuses,
    // A subscription engine counts too: it has no API status, only models.
    anyConfigured: Object.values(statuses).some((s) => s?.configured) || models.some((m) => isSubSpec(m.spec)),
    ready,
    recent,
    starred,
    setModel,
    toggleStar,
    reload,
  }
}
