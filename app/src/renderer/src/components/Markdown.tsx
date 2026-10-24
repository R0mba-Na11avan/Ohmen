import { createContext, memo, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Copy } from 'lucide-react'
import mermaid from 'mermaid'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import remarkMath from 'remark-math'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { renderSvg } from './DiagramTab'
import { normalizeMath } from './markdownMath'
// The source being rendered and whether it is still streaming in: lets a block tell
// "fence still open" from "finished" without the components object changing per token
// (a new object would remount every block — and re-render every diagram — each chunk).
const Source = createContext<{ text: string; streaming: boolean }>({ text: '', streaming: false })

// Plain text of a hast node — what a copy button should put on the clipboard.
interface HastNode {
  type: string
  value?: string
  tagName?: string
  properties?: { className?: unknown }
  children?: HastNode[]
  position?: { end: { offset?: number } }
}
const hastText = (n: HastNode): string => (n.type === 'text' ? (n.value ?? '') : (n.children ?? []).map(hastText).join(''))

// Fences opened minus fences closed is odd while the model is mid-block.
const hasOpenFence = (text: string) => ((text.match(/^ {0,3}(```|~~~)/gm) ?? []).length % 2) === 1


export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => setDone(false), 1400)
    return () => clearTimeout(t)
  }, [done])
  return (
    <button
      className="md-copy"
      title={done ? 'copied' : 'copy'}
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => setDone(true), () => {})
      }}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}
      {label && <span>{done ? 'Copied' : label}</span>}
    </button>
  )
}

function CodeBlock({ lang, raw, children, note }: { lang: string; raw: string; children: ReactNode; note?: string }) {
  return (
    <div className="md-code">
      <div className="md-code-head">
        <span>
          {lang || 'text'}
          {note && <span className="md-code-note"> · {note}</span>}
        </span>
        <CopyButton text={raw} label="Copy" />
      </div>
      <pre>{children}</pre>
    </div>
  )
}

function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'drawing' | 'ok' | 'error'>('drawing')
  const [view, setView] = useState<'diagram' | 'code'>('diagram')

  useEffect(() => {
    let alive = true
    setState('drawing')
    // parse first: a bad diagram fails quietly instead of leaving mermaid's error SVG behind
    mermaid
      .parse(code, { suppressErrors: true })
      .then((ok) => (ok ? renderSvg(code) : Promise.reject()))
      .then((svg) => {
        if (!alive || !ref.current) return
        ref.current.innerHTML = svg
        setState('ok')
      })
      .catch(() => alive && setState('error'))
    return () => {
      alive = false
    }
  }, [code])

  if (state === 'error')
    return (
      <CodeBlock lang="mermaid" raw={code} note="couldn't draw this diagram">
        <code>{code}</code>
      </CodeBlock>
    )

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-seg">
          <button className={view === 'diagram' ? 'on' : ''} onClick={() => setView('diagram')}>
            Diagram
          </button>
          <button className={view === 'code' ? 'on' : ''} onClick={() => setView('code')}>
            Code
          </button>
        </span>
        <CopyButton text={code} label="Copy" />
      </div>
      <div className="md-mermaid" style={{ display: view === 'diagram' ? undefined : 'none' }}>
        {state === 'drawing' && <span className="md-code-note">drawing…</span>}
        {/* the SVG goes in a node React renders empty, so its innerHTML never
            pulls a React-owned child (the "drawing…" note) out from under it */}
        <div ref={ref} />
      </div>
      {view === 'code' && (
        <pre>
          <code>{code}</code>
        </pre>
      )}
    </div>
  )
}

function Pre({ node, children }: { node?: HastNode; children?: ReactNode }) {
  const { text, streaming } = useContext(Source)
  const code = node?.children?.find((c) => c.tagName === 'code')
  const cls = code?.properties?.className
  const lang = (Array.isArray(cls) ? cls : []).map(String).find((c) => c.startsWith('language-'))?.slice(9) ?? ''
  const raw = code ? hastText(code).replace(/\n$/, '') : ''
  // a fence still open runs to the end of the source — its body is still arriving
  const open = streaming && hasOpenFence(text) && (node?.position?.end.offset ?? 0) >= text.trimEnd().length

  if (lang === 'mermaid' && !open) return <MermaidBlock code={raw} />
  return (
    <CodeBlock lang={lang} raw={raw} note={open && lang === 'mermaid' ? 'drawing…' : undefined}>
      {children}
    </CodeBlock>
  )
}

const components: Components = {
  pre: Pre as Components['pre'],
  // wide tables scroll inside the column instead of stretching it
  table: ({ children }) => (
    <div className="md-table">
      <table>{children}</table>
    </div>
  ),
  // the main process sends http(s) to the system browser (setWindowOpenHandler)
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
}

const remarkPlugins = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]] as never
// what the engineer types: a line break is a line break, as it was in the composer
const remarkPluginsTyped = [...(remarkPlugins as unknown[]), remarkBreaks] as never
// mermaid stays plain text: Pre hands it to the diagram renderer untouched; a
// formula KaTeX can't parse (or one still streaming in) shows as its source
const rehypePlugins = [
  [rehypeHighlight, { plainText: ['mermaid'] }],
  [rehypeKatex, { throwOnError: false, strict: false }],
] as never

export const Markdown = memo(function Markdown({
  text: raw,
  streaming = false,
  typed = false,
}: {
  text: string
  streaming?: boolean
  typed?: boolean
}) {
  // blocks find their end by offset into this text, so the context gets it too
  const text = normalizeMath(raw)
  return (
    <Source.Provider value={{ text, streaming }}>
      <ReactMarkdown
        remarkPlugins={typed ? remarkPluginsTyped : remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </Source.Provider>
  )
})
