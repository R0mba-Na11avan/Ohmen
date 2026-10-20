// Generate TypeScript types from the Python IR/Finding/PartsReport JSON-Schema.
// One contract: the pydantic models are the single source of truth, the frontend
// types are derived. Run: npm run gen-types  (needs the Python package importable).
import { compile } from 'json-schema-to-typescript'
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(here, '..', '..') // repo root (Python package lives here)
const OUT = resolve(here, '..', 'src', 'renderer', 'src', 'types')
const PY = process.env.PCB_PYTHON || (process.platform === 'win32' ? 'python' : 'python3')

const targets = [
  { fn: 'json_schema', root: 'Design', file: 'ir.ts' },
  { fn: 'finding_schema', root: 'Finding', file: 'findings.ts' },
  { fn: 'parts_report_schema', root: 'PartsReport', file: 'parts.ts' },
  { fn: 'bom_row_schema', root: 'BomRow', file: 'bom.ts' },
  { fn: 'bom_proposal_schema', root: 'BomProposal', file: 'bomProposal.ts' },
]

mkdirSync(OUT, { recursive: true })

for (const { fn, root, file } of targets) {
  const raw = execFileSync(
    PY,
    ['-c', `import json; from pcb_assistant.ir.schema import ${fn}; print(json.dumps(${fn}()))`],
    { cwd: ROOT, encoding: 'utf-8' },
  )
  const schema = JSON.parse(raw)
  const ts = await compile(schema, root, {
    bannerComment: `/* AUTO-GENERATED from the ${root} pydantic JSON-Schema. Do not edit. */`,
  })
  writeFileSync(resolve(OUT, file), ts)
  console.log(`wrote types/${file}`)
}
