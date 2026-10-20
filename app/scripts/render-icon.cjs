// Rasterize docs/assets/logo.svg into app/build/icon.png (1024 px), the icon
// electron-builder turns into the Windows .ico and the Linux icon set.
//   npx electron scripts/render-icon.cjs
const { app, BrowserWindow } = require('electron')
const { readFileSync, writeFileSync } = require('node:fs')
const { join } = require('node:path')

const SIZE = 1024
const svg = readFileSync(join(__dirname, '..', '..', 'docs', 'assets', 'logo.svg'), 'utf-8')
const out = join(__dirname, '..', 'build', 'icon.png')

app.disableHardwareAcceleration()
app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: SIZE,
    height: SIZE,
    show: false,
    transparent: true,
    frame: false,
    useContentSize: true,
    webPreferences: { offscreen: true, zoomFactor: 1 },
  })
  const html = `<html><body style="margin:0;background:transparent">${svg}</body></html>`
  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
  await new Promise((r) => setTimeout(r, 300))
  const image = await win.webContents.capturePage({ x: 0, y: 0, width: SIZE, height: SIZE })
  writeFileSync(out, image.resize({ width: SIZE, height: SIZE }).toPNG())
  console.log(`wrote ${out}`)
  app.quit()
})
