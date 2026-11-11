import { app, BrowserWindow, ipcMain, net, shell } from 'electron'
import { autoUpdater, type ProgressInfo, type UpdateInfo } from 'electron-updater'

// Updates from GitHub Releases (the publish block in electron-builder.yml).
//
// The Windows installer and the AppImage update themselves: electron-updater
// finds a newer published release, downloads it in the background, and installs
// it on the next quit (or now, from the "Restart to update" button). The .deb and
// .rpm builds, and dev runs, can't replace themselves, so they only learn that a
// newer release exists (from the GitHub API) and offer its download page.
//
// Checked shortly after launch, then every few hours, unless the user turned
// automatic checks off (Settings > About). Drafts and prereleases are never offered.

const OWNER = 'R0mba-Na11avan'
const REPO = 'Ohmen'
const RELEASES = `https://github.com/${OWNER}/${REPO}/releases`
const FIRST_CHECK_MS = 15_000
const EVERY_MS = 6 * 60 * 60 * 1000

export interface UpdateStatus {
  state: 'idle' | 'checking' | 'none' | 'available' | 'downloading' | 'ready' | 'error'
  current: string
  version?: string // the newer release
  percent?: number // downloading
  url: string // its release page (or the releases list)
  canInstall: boolean // this install updates itself
  message?: string // error
  checkedAt?: number // epoch ms of the last finished check
}

let status: UpdateStatus = {
  state: 'idle',
  current: app.getVersion(),
  url: `${RELEASES}/latest`,
  canInstall: false,
}
let timer: NodeJS.Timeout | null = null
let first: NodeJS.Timeout | null = null
let checking: Promise<void> | null = null

// Windows (NSIS) and AppImage can swap in the new version; deb/rpm need the package manager.
const selfUpdating = () => app.isPackaged && (process.platform === 'win32' || !!process.env.APPIMAGE)

function set(next: Partial<UpdateStatus>): void {
  status = { ...status, ...next }
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send('update:status', status)
}

// '0.1.10' > '0.1.9'; a prerelease sorts before its release.
export function newer(a: string, b: string): boolean {
  const parse = (v: string) => {
    const [core, pre] = v.trim().replace(/^v/i, '').split('-', 2)
    return { nums: core.split('.').map((n) => Number.parseInt(n, 10) || 0), pre: pre ?? '' }
  }
  const x = parse(a)
  const y = parse(b)
  for (let i = 0; i < Math.max(x.nums.length, y.nums.length); i++) {
    const d = (x.nums[i] ?? 0) - (y.nums[i] ?? 0)
    if (d) return d > 0
  }
  if (x.pre === y.pre) return false
  return x.pre === '' ? true : y.pre === '' ? false : x.pre > y.pre
}

// The latest published release, from the GitHub API (no install, just "there is one").
async function checkGithub(): Promise<void> {
  const r = await net.fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'Ohmen-update-check' },
  })
  if (r.status === 404) {
    set({ state: 'none', checkedAt: Date.now() }) // nothing published yet
    return
  }
  if (!r.ok) throw new Error(`GitHub answered ${r.status}`)
  const rel = (await r.json()) as { tag_name?: string; html_url?: string; draft?: boolean; prerelease?: boolean }
  const version = (rel.tag_name ?? '').replace(/^v/i, '')
  if (version && !rel.draft && !rel.prerelease && newer(version, status.current)) {
    set({ state: 'available', version, url: rel.html_url || `${RELEASES}/latest`, canInstall: false, checkedAt: Date.now() })
  } else {
    set({ state: 'none', checkedAt: Date.now() })
  }
}

async function runCheck(): Promise<void> {
  if (status.state === 'ready' || status.state === 'downloading') return // already on its way
  set({ state: 'checking', message: undefined })
  try {
    if (selfUpdating()) {
      try {
        await autoUpdater.checkForUpdates() // events below carry the result
        if (status.state === 'checking') set({ state: 'none', checkedAt: Date.now() })
        return
      } catch (e) {
        // e.g. a release without latest.yml: still say a newer one exists
        console.error('[updater] self-update check failed, asking GitHub:', e)
      }
    }
    await checkGithub()
  } catch (e) {
    set({ state: 'error', message: e instanceof Error ? e.message : String(e), checkedAt: Date.now() })
  }
}

export function checkForUpdates(): Promise<void> {
  checking ??= runCheck().finally(() => {
    checking = null
  })
  return checking
}

function schedule(on: boolean): void {
  if (timer) clearInterval(timer)
  if (first) clearTimeout(first)
  timer = first = null
  if (!on) return
  first = setTimeout(() => void checkForUpdates(), FIRST_CHECK_MS)
  timer = setInterval(() => void checkForUpdates(), EVERY_MS)
}

export function setUpdateChecks(on: boolean): void {
  schedule(on && app.isPackaged)
}

// `beforeInstall` runs just before the app quits into the installer.
export function initUpdater(opts: { automatic: boolean; beforeInstall: () => void }): void {
  if (selfUpdating()) {
    autoUpdater.autoDownload = true
    autoUpdater.autoInstallOnAppQuit = true
    autoUpdater.allowPrerelease = false
    autoUpdater.logger = console
    autoUpdater.on('update-available', (info: UpdateInfo) =>
      set({ state: 'downloading', version: info.version, percent: 0, url: `${RELEASES}/tag/v${info.version}`, canInstall: true }),
    )
    autoUpdater.on('download-progress', (p: ProgressInfo) => set({ state: 'downloading', percent: Math.round(p.percent) }))
    autoUpdater.on('update-downloaded', (info: UpdateInfo) =>
      set({ state: 'ready', version: info.version, canInstall: true, checkedAt: Date.now() }),
    )
    autoUpdater.on('update-not-available', () => set({ state: 'none', checkedAt: Date.now() }))
    autoUpdater.on('error', (e: Error) => {
      // a failed download after a found update: fall back to the download page
      if (status.state === 'downloading') {
        set({ state: 'available', canInstall: false, message: e.message })
      } else if (status.state !== 'checking') {
        set({ state: 'error', message: e.message, checkedAt: Date.now() })
      }
    })
  }

  ipcMain.handle('update:status', () => status)
  ipcMain.handle('update:check', async () => {
    await checkForUpdates()
    return status
  })
  ipcMain.handle('update:install', () => {
    if (status.state === 'ready' && selfUpdating()) {
      opts.beforeInstall()
      autoUpdater.quitAndInstall(true, true) // silent installer, then relaunch
    } else {
      shell.openExternal(status.url)
    }
  })

  // Dev runs never check on their own; Settings > About can still ask.
  schedule(opts.automatic && app.isPackaged)
}
