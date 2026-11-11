import type {
  EngineId,
  EngineSettings,
  EngineStatus,
  KicadCliStatus,
  LocalOverrides,
  ProviderId,
  ProviderStatus,
  SavedProvider,
  ServiceStatus,
} from './api'

export {}

interface AppSettings {
  selectedModel?: string
  recentModels?: string[]
  starredModels?: string[]
  onboardingDone?: boolean
  kicadCliPath?: string
  projectClick?: 'folder' | 'kicad'
  autoCheck?: boolean
  updateChecks?: boolean
}

// Mirrors UpdateStatus in src/main/updater.ts.
export interface UpdateStatus {
  state: 'idle' | 'checking' | 'none' | 'available' | 'downloading' | 'ready' | 'error'
  current: string
  version?: string
  percent?: number
  url: string
  canInstall: boolean // this install updates itself (Windows installer, AppImage)
  message?: string
  checkedAt?: number
}

declare global {
  interface Window {
    pcb: {
      getPort: () => Promise<number>
      getToken: () => Promise<string>
      sidecarReady: () => Promise<boolean>
      pickProject: () => Promise<string | null>
      newProject: () => Promise<string | null>
      revealProject: (source: string) => Promise<string>

      savedProviders: () => Promise<Partial<Record<ProviderId, SavedProvider>>>
      saveProvider: (
        provider: ProviderId,
        input: { apiKey?: string; baseUrl?: string; overrides?: LocalOverrides },
      ) => Promise<Record<ProviderId, ProviderStatus>>
      setLocalOverrides: (overrides: LocalOverrides) => Promise<Record<ProviderId, ProviderStatus>>
      removeProvider: (provider: ProviderId) => Promise<Record<ProviderId, ProviderStatus>>
      savedServices: () => Promise<Partial<Record<'digikey', { clientId: string; last4: string }>>>
      saveService: (
        service: 'digikey',
        input: { clientId: string; clientSecret: string },
      ) => Promise<Record<'digikey', ServiceStatus>>
      removeService: (service: 'digikey') => Promise<Record<'digikey', ServiceStatus>>
      getEngines: () => Promise<EngineSettings>
      saveEngines: (settings: EngineSettings) => Promise<Record<EngineId, EngineStatus>>
      pickEngineCli: (name: EngineId) => Promise<string | null>
      setKicadCli: (path: string) => Promise<KicadCliStatus> // '' = auto-detect
      pickKicadCli: () => Promise<string | null>
      appInfo: () => Promise<{ version: string; electron: string; logPath: string | null; keysProtected: boolean }>
      getSettings: () => Promise<AppSettings>
      setSettings: (partial: AppSettings) => Promise<AppSettings>

      updateStatus: () => Promise<UpdateStatus>
      checkForUpdates: () => Promise<UpdateStatus>
      installUpdate: () => Promise<void> // restart into a downloaded update, or open its download page
      onUpdateStatus: (cb: (status: UpdateStatus) => void) => () => void
    }
  }
}
