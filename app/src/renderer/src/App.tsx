import { useEffect, useState } from 'react'
import { Settings } from 'lucide-react'
import {
  autocheck,
  getBom,
  getDesignSummary,
  getLastReview,
  type DesignSummary,
  type ReviewResponse,
} from './api'
import { useModel } from './hooks/useModel'
import { ModelPicker } from './components/ModelPicker'
import { Onboarding } from './components/Onboarding'
import { SettingsModal, type SettingsTab } from './components/SettingsModal'
import { SyncBar, projectName } from './components/SyncBar'
import { FindingsList } from './components/FindingsList'
import { BomTable } from './components/BomTable'
import { DatasheetsTab } from './components/DatasheetsTab'
import { ChatPane } from './components/ChatPane'
import { DiagramTab } from './components/DiagramTab'
import { ErrorBoundary } from './components/ErrorBoundary'
import { StartScreen } from './components/StartScreen'
import { color, measure, space, type } from './theme'

type Tab = 'findings' | 'diagram' | 'bom' | 'datasheets' | 'chat'

export default function App() {
  const models = useModel()
  const { model } = models
  const [settingsTab, setSettingsTab] = useState<SettingsTab | null>(null) // null = closed
  const manageProviders = () => setSettingsTab('providers')
  const [onboardingDone, setOnboardingDone] = useState<boolean | null>(null)
  const [summary, setSummary] = useState<DesignSummary | null>(null)
  const [tab, setTab] = useState<Tab>('chat')
  const [refresh, setRefresh] = useState(0)

  // Findings: whatever the chat's checks/reviews and the auto-check recorded.
  const [result, setResult] = useState<ReviewResponse | null>(null)
  const [checking, setChecking] = useState(false)

  const [bomCount, setBomCount] = useState(0)

  const loaded = summary !== null

  // Switch project: play the workspace out (.bar-out / .sink-out) before
  // unmounting it, so the start screen doesn't just snap in.
  const [leaving, setLeaving] = useState(false)
  function switchProject() {
    if (leaving) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setSummary(null)
      return
    }
    setLeaving(true)
    setTimeout(() => {
      setSummary(null)
      setLeaving(false)
    }, 200)
  }

  useEffect(() => {
    window.pcb
      .getSettings()
      .then((s) => setOnboardingDone(!!s.onboardingDone))
      .catch(() => setOnboardingDone(true))
  }, [])

  function finishOnboarding() {
    setOnboardingDone(true)
    window.pcb.setSettings({ onboardingDone: true }).catch(() => {})
  }

  // First run, nothing connected (not even a dev .env key): offer setup first.
  const onboarding = models.ready && onboardingDone === false && !models.anyConfigured

  useEffect(() => {
    if (!loaded) {
      setBomCount(0)
      return
    }
    getBom().then((r) => setBomCount(r.length)).catch(() => {})
  }, [loaded, refresh])

  // Header counts follow the schematic as it's edited in KiCad: re-read on any
  // refresh and whenever the window regains focus (back from KiCad).
  useEffect(() => {
    if (!loaded) return
    const update = () =>
      getDesignSummary()
        .then((s) =>
          // Ignore a late answer after leaving or switching project.
          setSummary((prev) => (prev && (prev.source === s.source || (!!s.project_file && prev.project_file === s.project_file)) ? s : prev)),
        )
        .catch(() => {})
    update()
    window.addEventListener('focus', update)
    return () => window.removeEventListener('focus', update)
  }, [loaded, refresh])

  // Auto-check (Settings › General, opt-in): on opening the project and on
  // coming back from KiCad, re-run the free rule checks. The sidecar skips the
  // run when the schematic hasn't changed since they last ran.
  useEffect(() => {
    if (!loaded) return
    let live = true
    const run = async () => {
      const s = await window.pcb.getSettings().catch(() => null)
      if (!s?.autoCheck || !live) return
      setChecking(true)
      try {
        const r = await autocheck()
        if (live && r.ts) setResult(r)
      } catch {
        /* no schematic yet, or kicad-cli trouble: findings stay as they were */
      } finally {
        if (live) setChecking(false)
      }
    }
    run()
    window.addEventListener('focus', run)
    return () => {
      live = false
      window.removeEventListener('focus', run)
    }
  }, [loaded])

  // The Findings tab shows the project's findings as the chat's checks and
  // reviews (and the auto-check) left them (card-anywhere hybrid).
  async function loadLastReview() {
    try {
      const r = await getLastReview()
      if (r.ts) setResult(r) // any completed run, including a clean one
    } catch {
      /* none yet */
    }
  }

  function goTab(t: Tab) {
    setTab(t)
    setRefresh((x) => x + 1)
    if (t === 'findings') loadLastReview()
  }

  const tabs: Tab[] = ['chat', 'findings', 'diagram', 'bom', 'datasheets']
  const tabLabel = (t: Tab) => (t === 'bom' ? `bom${bomCount ? ` (${bomCount})` : ''}` : t)

  // The model picker lives under the chat composer once a project is open; the
  // start screen keeps it in the corner so a model can be picked up front.
  const settingsButton = (
    <button
      onClick={() => setSettingsTab('general')}
      aria-label="Settings"
      title="Settings"
      style={{ display: 'grid', placeItems: 'center', padding: 6 }}
    >
      <Settings size={15} strokeWidth={1.75} />
    </button>
  )

  return (
    <div
      className="dot-grid"
      style={{
        fontFamily: type.sans,
        height: '100vh',
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        background: color.benchVoid,
        color: color.inkPrimary,
      }}
    >
      {/* The header appears once a project is open; before that, only the
          app-wide controls float in the corner over the start screen. */}
      {summary ? (
        <div className={leaving ? 'bar-out' : undefined}>
          <SyncBar summary={summary} onSwitch={switchProject} right={settingsButton} />
        </div>
      ) : (
        <div
          style={{
            position: 'absolute',
            top: 10,
            right: 16,
            zIndex: 2,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <ModelPicker state={models} onManage={manageProviders} />
          {settingsButton}
        </div>
      )}
      {settingsTab && (
        <SettingsModal state={models} initialTab={settingsTab} onClose={() => setSettingsTab(null)} />
      )}

      {onboarding ? (
        <Onboarding state={models} onDone={finishOnboarding} />
      ) : !loaded ? (
        <StartScreen
          onLoaded={(s) => {
            setSummary(s)
            setResult(null)
            setRefresh((x) => x + 1)
          }}
        />
      ) : (
        <>
          {/* Header drops in (SyncBar), then these fade up after it — see .bar-in / .rise-in. */}
          <div
            className={leaving ? 'sink-out' : 'rise-in'}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '6px 16px',
              borderBottom: `1px solid ${color.benchLine}`,
              background: color.benchBase,
            }}
          >
            {tabs.map((t) => (
              <button
                key={t}
                onClick={() => goTab(t)}
                style={{
                  background: 'transparent',
                  border: 0,
                  borderBottom: `2px solid ${tab === t ? color.copper : 'transparent'}`,
                  borderRadius: 0,
                  color: tab === t ? color.inkPrimary : color.inkSecondary,
                  fontWeight: tab === t ? type.weight.med : type.weight.reg,
                  textTransform: 'capitalize',
                  padding: '5px 10px',
                }}
              >
                {tabLabel(t)}
              </button>
            ))}
          </div>

          {/* The Diagram tab is a full-bleed canvas (it pans itself); the rest scroll with padding. */}
          <div
            className={leaving ? 'sink-out' : 'rise-in-late'}
            style={{
              flex: 1,
              minHeight: 0,
              overflow: tab === 'diagram' ? 'hidden' : 'auto',
              padding: tab === 'diagram' ? 0 : `${space[5]}px ${space[6]}px ${space[7]}px`,
            }}
          >
            {/* Panes stay mounted (toggle visibility) so state survives tab switches. */}
            <div style={{ display: tab === 'findings' ? 'block' : 'none', maxWidth: measure.text, margin: '0 auto' }}>
              {checking && (
                <p style={{ fontSize: type.size.xs, color: color.inkMuted, margin: '0 0 12px' }}>Re-running rule checks…</p>
              )}
              {result ? (
                <ErrorBoundary label="the findings">
                  <FindingsList review={result} onChanged={loadLastReview} />
                </ErrorBoundary>
              ) : (
                <p style={{ color: color.inkMuted }}>
                  No findings yet. Ask in chat to run a check or a design review.
                </p>
              )}
            </div>

            <div style={{ display: tab === 'diagram' ? 'flex' : 'none', height: '100%' }}>
              <ErrorBoundary label="the diagram">
                <DiagramTab model={model} loaded={loaded} refreshKey={refresh} />
              </ErrorBoundary>
            </div>

            <div style={{ display: tab === 'bom' ? 'block' : 'none' }}>
              <ErrorBoundary label="the BOM">
                <BomTable refreshKey={refresh} onCount={setBomCount} />
              </ErrorBoundary>
            </div>

            <div style={{ display: tab === 'datasheets' ? 'block' : 'none', maxWidth: measure.text, margin: '0 auto' }}>
              <ErrorBoundary label="the datasheets">
                <DatasheetsTab refreshKey={refresh} />
              </ErrorBoundary>
            </div>

            <div style={{ display: tab === 'chat' ? 'flex' : 'none', height: '100%' }}>
              <ErrorBoundary label="the chat">
              <ChatPane
                models={models}
                projectName={summary ? projectName(summary) : null}
                onManageModels={manageProviders}
                onBomChanged={() => setRefresh((x) => x + 1)}
                onOpenFindings={() => goTab('findings')}
                onReviewRan={loadLastReview}
                onOpenDiagram={() => goTab('diagram')}
                onDiagramChanged={() => setRefresh((x) => x + 1)}
                onRoutesChanged={models.reload}
              />
              </ErrorBoundary>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
