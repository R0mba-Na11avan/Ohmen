// Provider keys + app settings, kept in Electron's per-user data dir (never the repo).
//
// Keys are encrypted with safeStorage (DPAPI on Windows, Keychain on macOS,
// libsecret on Linux) into credentials.json. Non-secret settings — the picked
// model, the local endpoint URL, per-model capability toggles — sit in plain
// settings.json. Raw keys flow renderer -> main once, on save, and main -> sidecar
// on every push; they never flow back to the renderer (it sees the last 4 chars).
import { app, safeStorage } from 'electron'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

export const PROVIDERS = ['claude', 'gemini', 'openai', 'local'] as const
export type Provider = (typeof PROVIDERS)[number]

// Per-model capability toggles for local models: { model_id: { tools, vision } }
export type Overrides = Record<string, { tools?: boolean; vision?: boolean }>

interface StoredKey {
  enc: string // base64 of the safeStorage ciphertext — or the plain key if !encrypted
  encrypted: boolean
  last4: string
}

export interface Settings {
  selectedModel?: string
  recentModels?: string[]
  starredModels?: string[]
  onboardingDone?: boolean
  kicadCliPath?: string // '' / absent = auto-detect
  projectClick?: 'folder' | 'kicad' // clicking the project name in the header
  autoCheck?: boolean // re-run the free rule checks when the schematic changes
  localBaseUrl?: string
  localOverrides?: Overrides
  engines?: EngineSettings
}

// --- subscription engines (Settings > Advanced) ---
// Nothing secret: the CLIs keep their own logins. Pushed to the sidecar as-is.

export const ENGINES = ['codex', 'claude'] as const
export type Engine = (typeof ENGINES)[number]
export type Route = 'api' | 'sub'

export interface EngineSettings {
  codex?: { enabled?: boolean; path?: string }
  claude?: { enabled?: boolean; path?: string; tosAck?: boolean }
  preferredRoute?: { claude?: Route; openai?: Route }
  fallbackToApi?: boolean // a spent subscription continues on the vendor's API key
}

const isRoute = (v: unknown): v is Route => v === 'api' || v === 'sub'

// Keep only well-formed fields from the renderer.
export function cleanEngineSettings(input: unknown): EngineSettings {
  const i = (input ?? {}) as Record<string, any>
  const engine = (e: any, claude: boolean) =>
    e && typeof e === 'object'
      ? {
          enabled: Boolean(e.enabled),
          path: typeof e.path === 'string' ? e.path.trim() : undefined,
          ...(claude && { tosAck: Boolean(e.tosAck) }),
        }
      : undefined
  const routes = i.preferredRoute ?? {}
  return {
    codex: engine(i.codex, false),
    claude: engine(i.claude, true),
    preferredRoute: {
      ...(isRoute(routes.claude) && { claude: routes.claude }),
      ...(isRoute(routes.openai) && { openai: routes.openai }),
    },
    fallbackToApi: Boolean(i.fallbackToApi),
  }
}

// The body for the sidecar's PUT /settings/engines.
export function enginesPayload(): Record<string, unknown> {
  const e = getSettings().engines ?? {}
  return {
    engines: {
      codex: { enabled: !!e.codex?.enabled, path: e.codex?.path || null },
      claude: { enabled: !!e.claude?.enabled, path: e.claude?.path || null, tos_ack: !!e.claude?.tosAck },
    },
    preferred_route: e.preferredRoute ?? {},
    fallback_to_api: !!e.fallbackToApi,
  }
}

export interface SavedProvider {
  last4?: string
  baseUrl?: string
  overrides?: Overrides
}

const dir = () => app.getPath('userData')
const credsPath = () => join(dir(), 'credentials.json')
const settingsPath = () => join(dir(), 'settings.json')

function readJson<T>(path: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as T
  } catch {
    return fallback
  }
}

function writeJson(path: string, data: unknown): void {
  mkdirSync(dir(), { recursive: true })
  writeFileSync(path, JSON.stringify(data, null, 2), 'utf-8')
}

const loadKeys = () => readJson<Partial<Record<Provider, StoredKey>>>(credsPath(), {})

export function getSettings(): Settings {
  return readJson<Settings>(settingsPath(), {})
}

export function setSettings(partial: Settings): Settings {
  const next = { ...getSettings(), ...partial }
  writeJson(settingsPath(), next)
  return next
}

function decrypt(k: StoredKey): string | null {
  try {
    return k.encrypted ? safeStorage.decryptString(Buffer.from(k.enc, 'base64')) : k.enc
  } catch {
    return null // e.g. the OS keyring changed; the user re-enters the key
  }
}

// False when saved keys are only obfuscated: on Linux without a desktop keyring
// (GNOME Keyring / KWallet) Chromium falls back to a fixed built-in password.
export function keysProtected(): boolean {
  if (!safeStorage.isEncryptionAvailable()) return false
  if (process.platform !== 'linux') return true
  return !['basic_text', 'unknown'].includes(safeStorage.getSelectedStorageBackend())
}

export function saveKey(provider: Provider, apiKey: string): void {
  const keys = loadKeys()
  const trimmed = apiKey.trim()
  // Without an OS keyring (some Linux setups) fall back to plaintext rather than
  // refusing to work; Windows/macOS always encrypt.
  const encrypted = safeStorage.isEncryptionAvailable()
  keys[provider] = {
    enc: encrypted ? safeStorage.encryptString(trimmed).toString('base64') : trimmed,
    encrypted,
    last4: trimmed.slice(-4),
  }
  writeJson(credsPath(), keys)
}

export function removeProvider(provider: Provider): void {
  const keys = loadKeys()
  delete keys[provider]
  writeJson(credsPath(), keys)
  if (provider === 'local') setSettings({ localBaseUrl: undefined, localOverrides: undefined })
}

// What the renderer may know: which providers are saved, never the keys.
export function savedProviders(): Partial<Record<Provider, SavedProvider>> {
  const keys = loadKeys()
  const s = getSettings()
  const out: Partial<Record<Provider, SavedProvider>> = {}
  for (const p of PROVIDERS) {
    if (p === 'local') {
      if (s.localBaseUrl) out.local = { baseUrl: s.localBaseUrl, overrides: s.localOverrides ?? {}, last4: keys.local?.last4 }
    } else if (keys[p]) {
      out[p] = { last4: keys[p]!.last4 }
    }
  }
  return out
}

// The body for the sidecar's PUT /settings/credentials (every provider, null = none saved).
export function credentialsPayload(): Record<Provider, Record<string, unknown> | null> {
  const keys = loadKeys()
  const s = getSettings()
  const key = (p: Provider) => (keys[p] ? decrypt(keys[p]!) : null)
  const cloud = (p: Provider) => {
    const k = key(p)
    return k ? { api_key: k } : null
  }
  return {
    claude: cloud('claude'),
    gemini: cloud('gemini'),
    openai: cloud('openai'),
    local: s.localBaseUrl
      ? { base_url: s.localBaseUrl, api_key: key('local'), overrides: s.localOverrides ?? {} }
      : null,
  }
}

// --- parts-distributor app registrations (DigiKey) ---
// Same encryption as provider keys, own file: these never list models.

export const SERVICES = ['digikey'] as const
export type Service = (typeof SERVICES)[number]

interface StoredService {
  clientId: string
  secret: StoredKey
}

const servicesPath = () => join(dir(), 'services.json')
const loadServices = () => readJson<Partial<Record<Service, StoredService>>>(servicesPath(), {})

export function isService(v: unknown): v is Service {
  return typeof v === 'string' && (SERVICES as readonly string[]).includes(v)
}

export function saveService(service: Service, clientId: string, clientSecret: string): void {
  const all = loadServices()
  const secret = clientSecret.trim()
  const encrypted = safeStorage.isEncryptionAvailable()
  all[service] = {
    clientId: clientId.trim(),
    secret: {
      enc: encrypted ? safeStorage.encryptString(secret).toString('base64') : secret,
      encrypted,
      last4: secret.slice(-4),
    },
  }
  writeJson(servicesPath(), all)
}

export function removeService(service: Service): void {
  const all = loadServices()
  delete all[service]
  writeJson(servicesPath(), all)
}

// The renderer sees the client id (not secret) and the secret's last 4.
export function savedServices(): Partial<Record<Service, { clientId: string; last4: string }>> {
  const out: Partial<Record<Service, { clientId: string; last4: string }>> = {}
  for (const [name, s] of Object.entries(loadServices())) {
    if (isService(name) && s) out[name] = { clientId: s.clientId, last4: s.secret.last4 }
  }
  return out
}

// The body for the sidecar's PUT /settings/services.
export function servicesPayload(): Record<Service, Record<string, string> | null> {
  const all = loadServices()
  const out = {} as Record<Service, Record<string, string> | null>
  for (const name of SERVICES) {
    const s = all[name]
    const secret = s ? decrypt(s.secret) : null
    out[name] = s && secret ? { client_id: s.clientId, client_secret: secret } : null
  }
  return out
}
