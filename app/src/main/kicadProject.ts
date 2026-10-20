import { shell } from 'electron'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import type { Settings } from './credentials'

// Create an empty KiCad project the way KiCad's own "New Project" does: a
// folder named after the project holding <name>.kicad_pro + a blank root
// <name>.kicad_sch. KiCad fills in every default the minimal .kicad_pro omits
// on first open, and pcbnew offers to create the board file when it's needed.
export function createKicadProject(chosen: string): string {
  const name = basename(chosen).replace(/\.kicad_pro$/i, '').trim()
  if (!name) throw new Error('Project name is empty')
  const dir = join(chosen.slice(0, chosen.length - basename(chosen).length), name)
  if (existsSync(dir) && readdirSync(dir).length > 0) {
    throw new Error(`Folder already exists and isn't empty: ${dir}`)
  }
  mkdirSync(dir, { recursive: true })

  const pro = { meta: { filename: `${name}.kicad_pro`, version: 1 } }
  writeFileSync(join(dir, `${name}.kicad_pro`), JSON.stringify(pro, null, 2) + '\n', 'utf-8')

  const sch = `(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "9.0")
\t(uuid "${randomUUID()}")
\t(paper "A4")
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
`
  writeFileSync(join(dir, `${name}.kicad_sch`), sch, 'utf-8')
  return dir
}

// Clicking the project name in the header: open its folder, or open it in
// KiCad (Settings > General). `source` is the loaded schematic, or the folder
// for a project with no schematic yet. Returns a note when it fell back to the
// folder, '' on success.
export async function revealProject(source: string, settings: Settings): Promise<string> {
  const dir = statSync(source).isDirectory() ? source : dirname(source)
  const openFolder = async () => {
    const err = await shell.openPath(dir)
    if (err) throw new Error(err)
  }
  if (settings.projectClick !== 'kicad') {
    await openFolder()
    return ''
  }

  const pro = readdirSync(dir).find((f) => f.toLowerCase().endsWith('.kicad_pro'))
  if (!pro) {
    await openFolder()
    return 'No .kicad_pro in this folder, so it opened the folder instead.'
  }
  const proPath = join(dir, pro)
  // Prefer the KiCad that sits next to the configured kicad-cli; otherwise
  // let the OS file association (set by the KiCad installer) pick it.
  const exe = settings.kicadCliPath
    ? join(dirname(settings.kicadCliPath), process.platform === 'win32' ? 'kicad.exe' : 'kicad')
    : ''
  if (exe && existsSync(exe)) {
    spawn(exe, [proPath], { detached: true, stdio: 'ignore' }).unref()
    return ''
  }
  const err = await shell.openPath(proPath)
  if (err) {
    await openFolder()
    return `Couldn't open KiCad (${err}), so it opened the folder instead.`
  }
  return ''
}
