// Download a datasheet the way a real browser would, for the sidecar.
//
// Some vendors (NXP, onsemi, ...) refuse scripted downloads — 403s, bot checks,
// even fake 404s — yet serve Chromium fine. The sidecar POSTs {url} here and gets
// back the path of the downloaded file. We fetch through Chromium's own network
// stack (session.fetch); if that returns a web page instead of a PDF (a bot
// check), a hidden window loads the page once so its cookies are set, then we
// fetch again. Localhost only, guarded by a per-launch token.
import { BrowserWindow, session } from 'electron'
import { randomBytes } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const MAX_BYTES = 40 * 1024 * 1024
const PAGE_WAIT_MS = 15_000
// One persistent session: cookies from a solved bot check carry over to the next fetch.
const PARTITION = 'persist:datasheets'

async function fetchPdf(url: string): Promise<Buffer | null> {
  const ses = session.fromPartition(PARTITION)
  // Two fetches and a page visit stay inside the sidecar's 45 s wait, so a file
  // is never written after it stopped listening.
  const r = await ses.fetch(url, {
    headers: { Accept: 'application/pdf,*/*;q=0.8' },
    signal: AbortSignal.timeout(12_000),
  })
  const body = Buffer.from(await r.arrayBuffer())
  if (body.length > MAX_BYTES) throw new Error('file too large')
  return body.subarray(0, 5).toString() === '%PDF-' ? body : null
}

// Let the page run (JS challenges, consent redirects) so the session earns its cookies.
function visit(url: string): Promise<void> {
  return new Promise((resolve) => {
    const win = new BrowserWindow({
      show: false,
      webPreferences: { session: session.fromPartition(PARTITION), plugins: false, sandbox: true },
    })
    const done = () => {
      clearTimeout(timer)
      if (!win.isDestroyed()) win.destroy()
      resolve()
    }
    const timer = setTimeout(done, PAGE_WAIT_MS)
    win.webContents.once('did-finish-load', () => setTimeout(done, 3000))
    win.loadURL(url).catch(() => {
      /* a PDF navigation or block page rejects here; the refetch decides */
    })
  })
}

async function fetchViaBrowser(url: string): Promise<string> {
  let pdf = await fetchPdf(url)
  if (!pdf) {
    await visit(url)
    pdf = await fetchPdf(url)
  }
  if (!pdf) throw new Error('the browser got a web page, not a PDF')
  const out = join(tmpdir(), `pcb-datasheet-${randomBytes(6).toString('hex')}.pdf`)
  await writeFile(out, pdf)
  return out
}

export interface BrowserFetchServer {
  url: string
  token: string
  server: Server
}

export function startBrowserFetchServer(): Promise<BrowserFetchServer> {
  const token = randomBytes(24).toString('hex')
  const server = createServer((req, res) => {
    const reply = (status: number, body: unknown) => {
      res.writeHead(status, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(body))
    }
    if (req.method !== 'POST' || req.url !== '/fetch') return reply(404, { error: 'not found' })
    if (req.headers['x-token'] !== token) return reply(403, { error: 'bad token' })
    let raw = ''
    req.setEncoding('utf-8')
    req.on('data', (c) => (raw += c))
    req.on('end', async () => {
      let target = ''
      try {
        target = String(JSON.parse(raw).url ?? '')
      } catch {
        return reply(400, { error: 'bad json' })
      }
      if (!/^https?:\/\//i.test(target)) return reply(400, { error: 'http(s) URLs only' })
      try {
        reply(200, { path: await fetchViaBrowser(target) })
      } catch (e) {
        reply(502, { error: e instanceof Error ? e.message : String(e) })
      }
    })
  })
  return new Promise((resolve, reject) => {
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      resolve({ url: `http://127.0.0.1:${port}/fetch`, token, server })
    })
  })
}
