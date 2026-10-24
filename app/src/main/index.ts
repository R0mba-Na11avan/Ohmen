import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from 'electron'
import { spawn, spawnSync, ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createServer } from 'node:net'
import { request } from 'node:http'
import { mkdirSync, openSync } from 'node:fs'
import { delimiter, join, resolve } from 'node:path'
import { startBrowserFetchServer, type BrowserFetchServer } from './browserFetch'
import { createKicadProject, revealProject } from './kicadProject'
import {
  PROVIDERS,
  cleanEngineSettings,
  credentialsPayload,
  enginesPayload,
  getSettings,
  isService,
  keysProtected,
  removeProvider,
  removeService,
  saveService,
  savedServices,
  servicesPayload,
  saveKey,
  savedProviders,
  setSettings,
  type Overrides,
  type Provider,
  type Settings,
} from './credentials'

// Dev: the sidecar runs from source with the developer's Python, from the repo
// root (out/main/index.js -> app/ -> root) so the root .env still works as a key
// fallback. Packaged: a frozen sidecar ships in resources/sidecar/ (see
// packaging/sidecar.spec) and no Python install is needed.
const PROJECT_ROOT = resolve(__dirname, '..', '..', '..')
const PYTHON = process.env.PCB_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')
const SIDECAR_EXE = join(
  process.resourcesPath,
  'sidecar',
  process.platform === 'win32' ? 'ohmen-sidecar.exe' : 'ohmen-sidecar',
)

// Per-launch secret: only this app (via preload) can talk to the sidecar, which
// holds the user's API keys. See pcb_assistant/server/auth.py.
const TOKEN = randomBytes(32).toString('hex')

let sidecar: ChildProcess | null = null
let sidecarPort = 0
// Resolves once the sidecar answers /healthz (false if it never did). The
// renderer's first call waits on it through get-port, so the window can open
// while a cold start (antivirus scanning the frozen sidecar) is still going.
let sidecarHealthy: Promise<boolean> = Promise.resolve(false)
let sidecarReady: Promise<unknown> = Promise.resolve()
let sidecarUp = false
let quitting = false

function findFreePort(): Promise<number> {
  return new Promise((res, rej) => {
    const srv = createServer()
    srv.on('error', rej)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      srv.close(() => res(port))
    })
  })
}

function startSidecar(port: number, browser: BrowserFetchServer | null): ChildProcess {
  const args = ['--port', String(port), '--log-level', 'warning']
  const [cmd, argv, cwd] = app.isPackaged
    ? [SIDECAR_EXE, args, app.getPath('userData')]
    : [PYTHON, ['-m', 'pcb_assistant.server', ...args], PROJECT_ROOT]
  // Packaged builds have no console: the sidecar's output goes to a log file
  // users can attach to a bug report (Settings > About shows where).
  const log = app.isPackaged ? openSync(sidecarLogPath(), 'w') : null
  const child = spawn(cmd, argv, {
    cwd,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PCB_SIDECAR_TOKEN: TOKEN,
      // Dev: this checkout's source wins over whatever pcb_assistant pip installed.
      ...(app.isPackaged
        ? {}
        : { PYTHONPATH: [join(PROJECT_ROOT, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) }),
      // Datasheet downloads the vendor refuses to scripts go through a hidden window.
      ...(browser ? { PCB_BROWSER_FETCH: browser.url, PCB_BROWSER_FETCH_TOKEN: browser.token } : {}),
    },
    stdio: log === null ? 'inherit' : ['ignore', log, log],
    windowsHide: true,
    // Its own process group on Linux, so quitting can stop the CLIs it started too.
    detached: process.platform !== 'win32',
  })
  child.on('error', (e) => console.error('[sidecar] failed to start:', e))
  child.on('exit', (code) => {
    console.log(`[sidecar] exited with ${code}`)
    if (child === sidecar && sidecarUp && !quitting) {
      sidecar = null
      void sidecarDied(code)
    }
  })
  return child
}

// Every request would fail from here on; say so once and offer a restart.
async function sidecarDied(code: number | null): Promise<void> {
  const r = await dialog.showMessageBox({
    type: 'error',
    title: 'Ohmen',
    message: 'Ohmen\'s engine stopped unexpectedly',
    detail:
      `It exited with code ${code}. Restart Ohmen to continue; your project files are safe.` +
      (app.isPackaged ? `\n\nDetails are in:\n${sidecarLogPath()}` : ''),
    buttons: ['Restart Ohmen', 'Close'],
    defaultId: 0,
    cancelId: 1,
  })
  if (r.response === 0) app.relaunch()
  app.exit(0)
}

function sidecarLogPath(): string {
  return join(app.getPath('logs'), 'sidecar.log')
}

function pingHealth(port: number): Promise<boolean> {
  return new Promise((res) => {
    const req = request({ host: '127.0.0.1', port, path: '/healthz', timeout: 800 }, (r) => {
      res(r.statusCode === 200)
      r.resume()
    })
    req.on('error', () => res(false))
    req.on('timeout', () => { req.destroy(); res(false) })
    req.end()
  })
}

// A first launch can be slow: Windows Defender scans the frozen sidecar before it
// runs. Give up early only if the process has already exited.
async function waitForHealth(port: number, child: ChildProcess, ms = 120_000): Promise<boolean> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) return false
    if (await pingHealth(port)) return true
    await new Promise((r) => setTimeout(r, 250))
  }
  return false
}

// Hand the saved keys to the sidecar, which validates each provider by listing
// its models. Resolves to the per-provider status (never echoes keys).
function pushCredentials(): Promise<unknown> {
  return sidecarPut('/settings/credentials', credentialsPayload())
}

// Parts-distributor app registrations (DigiKey): pushed the same way, status back.
function pushServices(): Promise<unknown> {
  return sidecarPut('/settings/services', servicesPayload())
}

// Subscription engines (Codex / Claude Code CLIs): settings in, detected status back.
function pushEngines(): Promise<unknown> {
  return sidecarPut('/settings/engines', enginesPayload())
}

// Point the sidecar at a kicad-cli ('' = auto-detect). Rejects with the sidecar's
// reason if the path isn't a working kicad-cli.
function pushKicadCli(path: string): Promise<unknown> {
  return sidecarPut('/settings/kicad', { path: path || null })
}

function sidecarPut(path: string, payload: unknown): Promise<unknown> {
  const body = JSON.stringify(payload)
  return new Promise((res, rej) => {
    const req = request(
      {
        host: '127.0.0.1',
        port: sidecarPort,
        path,
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
          'X-PCB-Token': TOKEN,
        },
      },
      (r) => {
        let data = ''
        r.setEncoding('utf-8')
        r.on('data', (c) => (data += c))
        r.on('end', () => {
          if (r.statusCode !== 200) return rej(new Error(sidecarError(data) ?? `${path} -> ${r.statusCode}: ${data}`))
          try {
            res(JSON.parse(data))
          } catch (e) {
            rej(e)
          }
        })
      },
    )
    req.on('error', rej)
    req.end(body)
  })
}

// FastAPI's {"detail": "..."} when it's a plain message (it's shown to the user).
function sidecarError(data: string): string | null {
  try {
    const d = JSON.parse(data).detail
    return typeof d === 'string' ? d : null
  } catch {
    return null
  }
}

const isProvider = (p: unknown): p is Provider => PROVIDERS.includes(p as Provider)

interface ProviderInput {
  apiKey?: string
  baseUrl?: string
  overrides?: Overrides
}

function registerSettingsIpc(): void {
  ipcMain.handle('get-token', () => TOKEN)
  // The renderer waits on this before its first /models call, so a tester's saved
  // keys are in the sidecar before it decides "no providers -> onboarding".
  ipcMain.handle('sidecar-ready', () => sidecarReady.then(() => true, () => true))
  ipcMain.handle('providers:saved', () => savedProviders())
  ipcMain.handle('providers:save', async (_e, provider: unknown, input: ProviderInput) => {
    if (!isProvider(provider)) throw new Error(`unknown provider ${String(provider)}`)
    if (provider === 'local') {
      const baseUrl = (input.baseUrl ?? '').trim()
      if (!baseUrl) throw new Error('a base URL is required for a local endpoint')
      setSettings({ localBaseUrl: baseUrl, localOverrides: input.overrides ?? getSettings().localOverrides ?? {} })
      if (input.apiKey?.trim()) saveKey('local', input.apiKey)
    } else {
      if (!input.apiKey?.trim()) throw new Error('an API key is required')
      saveKey(provider, input.apiKey)
    }
    return pushCredentials()
  })
  ipcMain.handle('providers:overrides', (_e, overrides: Overrides) => {
    setSettings({ localOverrides: overrides })
    return pushCredentials()
  })
  ipcMain.handle('providers:remove', (_e, provider: unknown) => {
    if (!isProvider(provider)) throw new Error(`unknown provider ${String(provider)}`)
    removeProvider(provider)
    return pushCredentials()
  })
  ipcMain.handle('services:saved', () => savedServices())
  ipcMain.handle('services:save', async (_e, service: unknown, input: { clientId?: string; clientSecret?: string }) => {
    if (!isService(service)) throw new Error(`unknown service ${String(service)}`)
    if (!input.clientId?.trim() || !input.clientSecret?.trim()) throw new Error('both the client id and secret are required')
    saveService(service, input.clientId, input.clientSecret)
    return pushServices()
  })
  ipcMain.handle('services:remove', (_e, service: unknown) => {
    if (!isService(service)) throw new Error(`unknown service ${String(service)}`)
    removeService(service)
    return pushServices()
  })
  ipcMain.handle('engines:get', () => getSettings().engines ?? {})
  ipcMain.handle('engines:save', (_e, input: unknown) => {
    setSettings({ engines: cleanEngineSettings(input) })
    return pushEngines()
  })
  ipcMain.handle('engines:pick', async (_e, name: unknown) => {
    const r = await dialog.showOpenDialog({
      title: `Locate the ${name === 'claude' ? 'Claude Code' : 'Codex'} CLI`,
      properties: ['openFile'],
    })
    return r.canceled ? null : r.filePaths[0]
  })
  ipcMain.handle('kicad:set', async (_e, path: unknown) => {
    if (typeof path !== 'string') throw new Error('kicad-cli path must be a string')
    const status = await pushKicadCli(path.trim()) // throws (nothing saved) if it doesn't run
    setSettings({ kicadCliPath: path.trim() || undefined })
    return status
  })
  ipcMain.handle('kicad:pick', async () => {
    const r = await dialog.showOpenDialog({
      title: 'Locate kicad-cli',
      properties: ['openFile'],
      filters: process.platform === 'win32' ? [{ name: 'kicad-cli', extensions: ['exe'] }] : [],
    })
    return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]
  })
  ipcMain.handle('app:info', () => ({
    version: app.getVersion(),
    electron: process.versions.electron,
    logPath: app.isPackaged ? sidecarLogPath() : null,
    keysProtected: keysProtected(),
  }))
  ipcMain.handle('settings:get', () => getSettings())
  // Only the non-secret UI settings are writable from the renderer.
  ipcMain.handle('settings:set', (_e, partial: Settings) =>
    setSettings({
      ...(partial.selectedModel !== undefined && { selectedModel: partial.selectedModel }),
      ...(isSpecList(partial.recentModels) && { recentModels: partial.recentModels }),
      ...(isSpecList(partial.starredModels) && { starredModels: partial.starredModels }),
      ...(partial.onboardingDone !== undefined && { onboardingDone: partial.onboardingDone }),
      ...((partial.projectClick === 'folder' || partial.projectClick === 'kicad') && { projectClick: partial.projectClick }),
      ...(typeof partial.autoCheck === 'boolean' && { autoCheck: partial.autoCheck }),
    }),
  )
}

function isSpecList(v: unknown): v is string[] {
  return Array.isArray(v) && v.length <= 100 && v.every((s) => typeof s === 'string')
}

async function createWindow(): Promise<void> {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    webPreferences: {
      preload: resolve(__dirname, '..', 'preload', 'index.js'),
      // The preload only bridges IPC (contextBridge + ipcRenderer), so the
      // renderer runs fully sandboxed, isolated, with no Node access.
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  // The window only ever shows the app. A file dropped outside a drop zone would
  // otherwise navigate it to that file, with no way back short of a restart.
  win.webContents.on('will-navigate', (e, url) => {
    const here = win.webContents.getURL().split('#')[0]
    if (url.split('#')[0] !== here) e.preventDefault()
  })

  // External links (datasheets, distributor pages) open in the system browser —
  // never a new in-app window (which would have no preload -> window.pcb undefined).
  win.webContents.setWindowOpenHandler(({ url }) => {
    // A stored datasheet opens in-app with Chromium's PDF viewer (honours #page=N);
    // its URL carries the sidecar token, so it must never reach the system browser.
    if (url.startsWith(`http://127.0.0.1:${sidecarPort}/datasheets/`)) {
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          width: 900,
          height: 1000,
          autoHideMenuBar: true,
          webPreferences: { plugins: true, sandbox: true, preload: undefined },
        },
      }
    }
    if (url.startsWith('http:') || url.startsWith('https:')) shell.openExternal(url)
    return { action: 'deny' }
  })

  // Electron spellchecks but ships no context menu, so misspellings got a squiggle
  // with no way to fix them. Right-click: suggestions first, then edit actions.
  win.webContents.on('context-menu', (_e, params) => {
    const wc = win.webContents
    const items: Electron.MenuItemConstructorOptions[] = []
    if (params.misspelledWord) {
      for (const s of params.dictionarySuggestions) {
        items.push({ label: s, click: () => wc.replaceMisspelling(s) })
      }
      if (params.dictionarySuggestions.length === 0) items.push({ label: 'No suggestions', enabled: false })
      items.push(
        {
          label: 'Add to dictionary',
          click: () => wc.session.addWordToSpellCheckerDictionary(params.misspelledWord),
        },
        { type: 'separator' },
      )
    }
    if (params.isEditable) {
      items.push({ role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { type: 'separator' }, { role: 'selectAll' })
    } else if (params.selectionText) {
      items.push({ role: 'copy' })
    }
    if (items.length) Menu.buildFromTemplate(items).popup({ window: win })
  })

  // electron-vite sets ELECTRON_RENDERER_URL in dev; load the built file otherwise.
  const devUrl = process.env.ELECTRON_RENDERER_URL

  // No File/Edit/View menu bar (Windows/Linux) — nothing in it belongs to this app.
  // Text-field shortcuts (copy/paste/undo) are Chromium's and keep working. The
  // menu used to carry reload + devtools; keep those as dev-only shortcuts.
  if (process.platform !== 'darwin') {
    win.removeMenu()
    if (devUrl) {
      win.webContents.on('before-input-event', (_e, input) => {
        if (input.type !== 'keyDown') return
        const key = input.key.toLowerCase()
        if (input.key === 'F12' || (input.control && input.shift && key === 'i')) win.webContents.toggleDevTools()
        else if (input.control && key === 'r') win.webContents.reload()
      })
    }
  }
  if (devUrl) {
    await win.loadURL(devUrl)
  } else {
    await win.loadFile(resolve(__dirname, '..', 'renderer', 'index.html'))
  }
}

// Stops the sidecar and whatever it started (kicad-cli, agent CLIs).
function killSidecar(): void {
  const child = sidecar
  sidecar = null
  if (!child || child.pid === undefined || child.exitCode !== null) return
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true })
    } else {
      process.kill(-child.pid, 'SIGTERM')
    }
  } catch {
    child.kill()
  }
}

app.whenReady().then(async () => {
  ipcMain.handle('get-port', async () => {
    await sidecarHealthy
    return sidecarPort
  })
  ipcMain.handle('pick-project', async () => {
    const r = await dialog.showOpenDialog({
      title: 'Open a KiCad project folder',
      properties: ['openDirectory'],
    })
    return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]
  })
  ipcMain.handle('new-project', async () => {
    const r = await dialog.showSaveDialog({
      title: 'Create a KiCad project',
      defaultPath: join(app.getPath('documents'), 'new_board'),
      buttonLabel: 'Create',
      nameFieldLabel: 'Project name',
      properties: ['createDirectory', 'showOverwriteConfirmation'],
    })
    return r.canceled || !r.filePath ? null : createKicadProject(r.filePath)
  })
  ipcMain.handle('project:reveal', (_e, source: unknown) => {
    if (typeof source !== 'string') throw new Error('project path must be a string')
    return revealProject(source, getSettings())
  })

  registerSettingsIpc()

  if (app.isPackaged) mkdirSync(app.getPath('logs'), { recursive: true })
  sidecarPort = await findFreePort()
  const browser = await startBrowserFetchServer().catch((e) => {
    console.error('[browser-fetch] unavailable:', e)
    return null
  })
  const child = startSidecar(sidecarPort, browser)
  sidecar = child
  sidecarHealthy = waitForHealth(sidecarPort, child)
  // Not awaited: startup and listing models can take a while; the window opens meanwhile.
  sidecarReady = sidecarHealthy.then((healthy) => {
    if (!healthy) {
      console.error('[sidecar] did not become healthy in time')
      if (app.isPackaged) {
        dialog.showErrorBox(
          'Ohmen could not start its engine',
          `The background service did not start. Details are in:\n${sidecarLogPath()}\n\n` +
            'Please include that file if you report this on GitHub.',
        )
      }
      return
    }
    sidecarUp = true
    const { kicadCliPath } = getSettings()
    return Promise.all([
      pushCredentials().catch((e) => console.error('[sidecar] credential push failed:', e)),
      pushServices().catch((e) => console.error('[sidecar] services push failed:', e)),
      pushEngines().catch((e) => console.error('[sidecar] engines push failed:', e)),
      // A saved path that stopped working falls back to auto-detect; Settings shows which is in use.
      kicadCliPath && pushKicadCli(kicadCliPath).catch((e) => console.error('[sidecar] kicad-cli push failed:', e)),
    ])
  })

  await createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  killSidecar()
  if (process.platform !== 'darwin') app.quit()
})
app.on('before-quit', () => {
  quitting = true
  killSidecar()
})
app.on('quit', killSidecar)
