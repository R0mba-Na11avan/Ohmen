// Typed client for the local Python sidecar. The port is assigned at runtime by
// the Electron main process and handed over through the preload bridge.
import type { Finding } from './types/findings'
import type { PartsReport } from './types/parts'
import type { BomRow } from './types/bom'
import type { BomProposal, Offer } from './types/bomProposal'
import { getEffort } from './effort'
import { getReviewDepth } from './reviewDepth'

export type ProviderId = 'claude' | 'gemini' | 'openai' | 'local'
// Subscription providers: the user's own logged-in CLI runs the model.
export type SubProviderId = 'claude-sub' | 'codex-sub'
export type Route = 'api' | 'sub'

export interface ModelInfo {
  spec: string // the route the user would get now (their preferred route if it serves this model)
  provider: ProviderId | SubProviderId
  model: string
  cost_tier: string
  supports_pdf: boolean
  supports_tools: boolean
  supports_vision: boolean
  supports_thinking: boolean // takes the composer's effort setting
  recommended: boolean
  context_tokens: number
  notes: string
  vendor: string // 'claude' | 'openai' | 'gemini' | 'local'
  routes: Partial<Record<Route, string>> // every way to run this model -> its spec
}

export const isSubSpec = (spec: string) => spec.startsWith('claude-sub:') || spec.startsWith('codex-sub:')

// --- subscription engines (Codex / Claude Code CLIs) ---
export type EngineId = 'codex' | 'claude'

export interface EngineStatus {
  enabled: boolean
  installed: boolean
  path: string | null
  version: string | null
  logged_in: boolean | null // null = couldn't tell
  detail: string
}

// Stored by the main process (settings.json); nothing secret.
export interface EngineSettings {
  codex?: { enabled?: boolean; path?: string }
  claude?: { enabled?: boolean; path?: string; tosAck?: boolean }
  preferredRoute?: { claude?: Route; openai?: Route }
  fallbackToApi?: boolean
}

export const getEngineStatus = (refresh = false) =>
  get<Record<EngineId, EngineStatus>>(`/settings/engines${refresh ? '?refresh=true' : ''}`)
export const startEngineLogin = (engine: EngineId) =>
  postJson<{ ok: boolean; detail: string }>(`/settings/engines/${engine}/login`, {})

// Which kicad-cli ingestion would use right now.
export interface KicadCliStatus {
  path: string | null
  source: 'custom' | 'env' | 'path' | 'auto' | null
  version: string | null
  error: string | null
}

// Sidecar's view of a provider: is a key active, where from, does it work.
export interface ProviderStatus {
  configured: boolean
  source: 'saved' | 'env' | null
  ok: boolean
  error: string
  models: string[]
}

// Main process's view: what's saved in Settings (never the key itself).
export interface SavedProvider {
  last4?: string
  baseUrl?: string
  overrides?: LocalOverrides
}

export type LocalOverrides = Record<string, { tools?: boolean; vision?: boolean }>

export interface DesignSummary {
  source: string
  project_file?: string | null // the .kicad_pro, when there is one
  components: number
  nets: number
  meta: Record<string, string>
}

export interface CoverageItem {
  ref: string
  part: string
  checks: number
}
export interface RunInfo {
  ts: number
  stale: boolean // the schematic changed since it ran
  model: string // the AI review's model (review runs only)
}
export interface ReviewResponse {
  findings: Finding[] // current, not dismissed
  muted: number // = dismissed.length
  dismissed: Finding[]
  model: string // the last AI review's model, '' if none ran
  ts: number // latest run of anything; 0 = nothing has run yet
  coverage: CoverageItem[]
  components: number
  runs: { rules?: RunInfo; review?: RunInfo } // rules = patterns + ERC/DRC + lifecycle; review = AI
  dropped: Finding[] // AI findings the last review didn't raise again
}

let cachedBase = ''
let cachedToken = ''

async function base(): Promise<string> {
  if (!cachedBase) {
    const port = await window.pcb.getPort()
    cachedBase = `http://127.0.0.1:${port}`
  }
  return cachedBase
}

// The sidecar only answers this app: every call carries the per-launch token.
async function token(): Promise<string> {
  if (!cachedToken) cachedToken = await window.pcb.getToken()
  return cachedToken
}

async function authed(extra?: Record<string, string>): Promise<Record<string, string>> {
  return { 'X-PCB-Token': await token(), ...extra }
}

// For URLs the browser fetches itself (<img src>), where no header can be set.
async function tokenUrl(path: string): Promise<string> {
  return `${await base()}${path}?token=${encodeURIComponent(await token())}`
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${await base()}${path}`, { headers: await authed() })
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`)
  return (await r.json()) as T
}

async function reqJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${await base()}${path}`, {
    method,
    headers: await authed(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw new Error(await errText(r, path))
  return (await r.json()) as T
}

const postJson = <T>(path: string, body: unknown) => reqJson<T>('POST', path, body)

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const r = await fetch(`${await base()}${path}`, { method: 'POST', body: form, headers: await authed() })
  if (!r.ok) throw new Error(await errText(r, path))
  return (await r.json()) as T
}

async function errText(r: Response, path: string): Promise<string> {
  try {
    const d = await r.json()
    return `POST ${path} -> ${r.status}: ${d.detail ?? JSON.stringify(d)}`
  } catch {
    return `POST ${path} -> ${r.status}`
  }
}

export const getHealth = () => get<{ status: string }>('/healthz')
export const getModels = () => get<ModelInfo[]>('/models')
export const refreshModels = () => postJson<Record<ProviderId, ProviderStatus>>('/models/refresh', {})
export const getProviderStatus = () => get<Record<ProviderId, ProviderStatus>>('/settings/providers')
export const getKicadCli = () => get<KicadCliStatus>('/settings/kicad')

export const sync = (path: string) => postJson<DesignSummary>('/sync', { path })
export const getDesignSummary = () => get<DesignSummary>('/design/summary')
export const getLastReview = () => get<ReviewResponse>('/review/last')
// Re-runs the free rule checks only if the schematic changed since they last ran.
export const autocheck = () => postJson<ReviewResponse>('/findings/autocheck', {})
export const mute = (key: string, reason = '') =>
  postJson<{ ok: boolean }>('/mute', { key, reason })
export const unmute = (key: string) => postJson<{ ok: boolean }>('/unmute', { key })

export const parts = (requirements: string, model: string, n = 5) =>
  postJson<PartsReport>('/parts', { requirements, model, n })

export const chat = (message: string, model: string) =>
  postJson<{ answer: string }>('/chat', { message, model })

export interface DiagramResponse {
  purpose: string
  mermaid: string
  note?: string
  has_previous?: boolean
}

export const diagram = (model: string) => postJson<DiagramResponse>('/diagram', { model })
export const getLastDiagram = () => get<DiagramResponse>('/diagram/last')
export const revertDiagram = () => postJson<DiagramResponse>('/diagram/revert', {})

// --- BOM artifact ---
export type { BomRow }
export const getBom = () => get<BomRow[]>('/bom')
export const fillBom = () => postJson<BomRow[]>('/bom/fill', {})
export const addBomRow = (row: Partial<BomRow>) => postJson<BomRow>('/bom/row', row)
export const updateBomRow = (id: string, fields: Partial<BomRow>) =>
  reqJson<BomRow>('PATCH', `/bom/row/${id}`, fields)
export const deleteBomRow = (id: string) => reqJson<{ ok: boolean }>('DELETE', `/bom/row/${id}`)
export const mergeBomRows = (sourceId: string, intoId: string) =>
  postJson<BomRow>('/bom/merge', { source_id: sourceId, into_id: intoId })
export const keepBomRowSeparate = (id: string) =>
  postJson<BomRow>(`/bom/row/${encodeURIComponent(id)}/keep-separate`, {})

// DigiKey fill: proposals wait for approval; only approve writes the BOM.
export type { BomProposal, Offer }
export const getBomProposals = () => get<BomProposal[]>('/bom/proposals')
export const proposeBomFill = (rowIds?: string[]) =>
  postJson<BomProposal[]>('/bom/propose', { row_ids: rowIds ?? null })
export const approveBomProposal = (id: string, choice = 0) =>
  postJson<BomRow>(`/bom/proposals/${encodeURIComponent(id)}/approve`, { choice })
export const rejectBomProposal = (id: string) =>
  postJson<BomProposal>(`/bom/proposals/${encodeURIComponent(id)}/reject`, {})
export const approveAllBomProposals = () => postJson<BomRow[]>('/bom/proposals/approve-all', {})

// A footprint the agent generated from a datasheet, waiting on the engineer.
export interface FootprintDim {
  name: string
  min: number | null
  nom: number | null
  max: number | null
  source: string
}
export interface FootprintPad {
  number: string
  x: number
  y: number
  w: number
  h: number
  drill: number | null
}
export interface FootprintProposal {
  id: string
  part: string
  name: string
  family: string
  package: string
  drawing: string
  dims: FootprintDim[]
  pads?: FootprintPad[] // the lands as built (older proposals have none)
  warnings: string[]
  stock: string[] // matching footprints already in KiCad's library
  status: 'pending' | 'accepted' | 'rejected'
  file: string | null
}
export const getFootprint = (id: string) => get<FootprintProposal>(`/footprints/${encodeURIComponent(id)}`)
export const footprintPreviewUrl = (id: string) => tokenUrl(`/footprints/${encodeURIComponent(id)}/preview.svg`)
export const acceptFootprint = (id: string) =>
  postJson<FootprintProposal>(`/footprints/${encodeURIComponent(id)}/accept`, {})
export const rejectFootprint = (id: string) =>
  postJson<FootprintProposal>(`/footprints/${encodeURIComponent(id)}/reject`, {})

// --- datasheet library (<project>/datasheets/) ---
export interface DatasheetAttempt {
  source: string
  url?: string | null
  error: string
}
export interface DatasheetEntry {
  key: string
  refs: string[]
  status: 'ok' | 'missing' | 'mismatch'
  file?: string | null
  source?: string | null // schematic | manufacturer | digikey | upload
  url?: string | null
  pages: number
  tried: DatasheetAttempt[]
  fetched_at: number
}
export const getDatasheets = () => get<DatasheetEntry[]>('/datasheets')
export const fetchDatasheets = (key?: string, refresh = false) =>
  postJson<DatasheetEntry[]>('/datasheets/fetch', { key: key ?? null, refresh })
export function uploadDatasheet(key: string, file: File): Promise<DatasheetEntry> {
  const form = new FormData()
  form.set('key', key)
  form.set('file', file)
  return postForm<DatasheetEntry>('/datasheets/upload', form)
}
export const datasheetUrl = (key: string, page?: number) =>
  tokenUrl(`/datasheets/${encodeURIComponent(key)}/file`).then((u) => (page ? `${u}#page=${page}` : u))

// --- parts-distributor credentials (Settings > Parts sources) ---
export interface ServiceStatus {
  configured: boolean
  source?: string | null
  error?: string | null
  detail?: string | null // what a successful Test saw
}
export const getServices = () => get<Record<'digikey', ServiceStatus>>('/settings/services')
export const testDigikey = () => postJson<ServiceStatus>('/settings/services/digikey/test', {})

// Fetch the BOM as CSV and trigger a download in-app (no external browser).
export async function downloadBomCsv(): Promise<void> {
  const r = await fetch(`${await base()}/bom.csv`, { headers: await authed() })
  if (!r.ok) throw new Error(await errText(r, '/bom.csv'))
  const blob = await r.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'bom.csv'
  a.click()
  URL.revokeObjectURL(url)
}

// --- chat sessions (per project, Claude-Desktop style) ---
export interface ChatSessionSummary {
  id: string
  title: string
  created_at: number
  updated_at: number
  turns: number
}
// An image attached to a chat turn; `url` is where the renderer can show it from.
export interface ChatImage {
  id: string
  name: string
  url: string
}
export const CHAT_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

// One reviewer on a team review, as the sidecar reports it (review_progress frames).
export interface ReviewerRow {
  reviewer: string
  focus?: string
  status: 'planning' | 'started' | 'done' | 'failed'
  findings?: number
  ms?: number
  error?: string
  steps?: string[] // what it did, in order ('U3: reading TJA1051TK-3 datasheet p.4'); '⚑ ' = a finding
  last?: string | null // latest step, shown inline while it works
  requests?: number
  tokens_in?: number
  tokens_cached?: number
  tokens_cache_write?: number
  tokens_out?: number
  t0?: number // client clock when it started, for the live timer (not persisted)
}

// A review_progress event: lifecycle (planning/started/done/failed) or an
// addition to a reviewer's row (step/finding/usage).
export interface ReviewProgress {
  reviewer: string
  status: ReviewerRow['status'] | 'step' | 'finding' | 'usage'
  focus?: string
  summary?: string
  tool?: string
  findings?: number
  ms?: number
  error?: string
  requests?: number
  tokens_in?: number
  tokens_cached?: number
  tokens_cache_write?: number
  tokens_out?: number
}

export interface ChatTurn {
  q: string
  a: string
  thinking?: string
  tools: { kind: 'call' | 'result'; name: string; info: string }[]
  team?: ReviewerRow[]
  images?: ChatImage[]
  error?: string | null
  stopped?: boolean
  usage?: TurnUsage | null
}

// What a chat turn cost, as the sidecar logs it (.pcb_assistant/usage.jsonl).
export interface TurnUsage {
  requests: number
  tokens_fresh: number // input neither read from nor written to the prompt cache
  tokens_cache_write: number
  tokens_cached: number
  tokens_out: number
  max_context: number // the largest single request's input
}
export interface ChatSessionDetail {
  id: string
  title: string
  created_at: number
  updated_at: number
  transcript: ChatTurn[]
}

export const listChats = () => get<ChatSessionSummary[]>('/chats')
export const newChat = () => postJson<ChatSessionSummary>('/chats', {})
export async function getChat(id: string): Promise<ChatSessionDetail> {
  const d = await get<ChatSessionDetail>(`/chats/${id}`)
  for (const t of d.transcript) {
    t.images = await Promise.all(
      (t.images ?? []).map(async (i) => ({ ...i, url: await tokenUrl(`/chats/images/${i.id}`) })),
    )
  }
  return d
}
export async function uploadChatImage(file: File): Promise<ChatImage> {
  const form = new FormData()
  form.set('file', file)
  const img = await postForm<{ id: string; name: string }>('/chats/images', form)
  return { ...img, url: await tokenUrl(`/chats/images/${img.id}`) }
}
export const deleteChat = (id: string) => reqJson<{ ok: boolean }>('DELETE', `/chats/${id}`)

// --- streaming chat agent (WebSocket) ---
export interface FindingCounts {
  must_fix: number
  verify: number
  cosmetic: number
  muted: number
}

export interface ChatHandlers {
  onSession?(chatId: string): void
  onThinking?(text: string): void
  onToken?(text: string): void
  onTool?(kind: 'call' | 'result', name: string, info: string): void
  onReviewProgress?(event: ReviewProgress): void
  onBom?(rows: BomRow[]): void
  onFindings?(counts: FindingCounts): void
  onDiagram?(mermaid: string, note: string): void
  onFootprint?(id: string): void
  onNotice?(text: string): void
  onRateLimit?(info: RateLimitInfo): void
  onUsage?(usage: TurnUsage): void
  onDone?(): void
  onError?(detail: string, info?: ChatErrorInfo): void
}

// A subscription's usage window, as its CLI reports it.
export interface RateLimitInfo {
  utilization: number | null // 0..1 of the window used
  resets_at: number | null // epoch seconds
  window: string
}

export interface ChatErrorInfo {
  code?: 'usage_limit'
  resets_at?: number | null
}

export async function streamChat(
  message: string,
  images: ChatImage[],
  model: string,
  chatId: string | null,
  h: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const port = await window.pcb.getPort()
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws/chat?token=${encodeURIComponent(await token())}`)
  // Exactly one of onDone/onError ends a turn, so the composer always frees up.
  let ended = false
  const end = (detail?: string, info?: ChatErrorInfo) => {
    if (ended) return
    ended = true
    if (detail === undefined) h.onDone?.()
    else h.onError?.(detail, info)
    ws.close()
  }
  ws.onopen = () =>
    ws.send(
      JSON.stringify({
        message,
        model,
        chat_id: chatId,
        review_depth: getReviewDepth(),
        effort: getEffort(),
        images: images.map(({ id, name }) => ({ id, name })),
      }),
    )
  ws.onmessage = (e) => {
    if (ended) return // stopped: frames still in flight are dropped
    const f = JSON.parse(e.data)
    switch (f.type) {
      case 'session':
        h.onSession?.(f.chat_id)
        break
      case 'thinking':
        h.onThinking?.(f.text)
        break
      case 'token':
        h.onToken?.(f.text)
        break
      case 'tool_call':
        h.onTool?.('call', f.name, typeof f.args === 'string' ? f.args : JSON.stringify(f.args))
        break
      case 'tool_result':
        h.onTool?.('result', f.name, f.summary)
        break
      case 'review_progress': {
        const { type: _type, ...event } = f
        h.onReviewProgress?.(event)
        break
      }
      case 'bom':
        h.onBom?.(f.rows)
        break
      case 'findings':
        h.onFindings?.(f.counts)
        break
      case 'diagram':
        h.onDiagram?.(f.mermaid, f.note)
        break
      case 'footprint':
        h.onFootprint?.(f.id)
        break
      case 'notice':
        h.onNotice?.(f.text)
        break
      case 'rate_limit':
        h.onRateLimit?.({ utilization: f.utilization ?? null, resets_at: f.resets_at ?? null, window: f.window })
        break
      case 'usage': {
        const { type: _type, ...usage } = f
        h.onUsage?.(usage)
        break
      }
      case 'done':
        end()
        break
      case 'error':
        end(f.detail, { code: f.code, resets_at: f.resets_at })
        break
    }
  }
  // Stop: closing the socket cancels the turn in the sidecar. The caller has
  // already wrapped up the turn, so no done/error follows.
  const stop = () => {
    ended = true
    ws.close()
  }
  if (signal?.aborted) stop()
  else signal?.addEventListener('abort', stop)
  ws.onerror = () => end('Lost the connection to the Ohmen engine.')
  ws.onclose = () => end('The reply was cut off: the connection to the Ohmen engine closed.')
}
