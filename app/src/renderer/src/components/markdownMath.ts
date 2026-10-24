// remark-math reads only $$: inline $$x$$ within a line, display on lines of their
// own. Single $ stays text (prices). Models also write \( \) and \[ \], which
// markdown would unescape to bare brackets, so those become $$ first. Code is left
// alone: fenced blocks line by line, inline spans by their backticks.
export function normalizeMath(text: string): string {
  if (!text.includes('\\(') && !text.includes('\\[')) return text
  const out: string[] = []
  let prose: string[] = []
  let fence: string | null = null
  const flush = () => {
    if (prose.length) out.push(mathInProse(prose.join('\n')))
    prose = []
  }
  for (const line of text.split('\n')) {
    const marker = line.match(/^ {0,3}(```|~~~)/)?.[1]
    if (fence) {
      out.push(line)
      if (marker === fence) fence = null
    } else if (marker) {
      flush()
      fence = marker
      out.push(line)
    } else prose.push(line)
  }
  flush()
  return out.join('\n')
}

const mathInProse = (text: string) =>
  text
    .split(/(`+[^`]*`+)/)
    .map((part, i) =>
      i % 2
        ? part
        : part
            .replace(/\\\[([\s\S]+?)\\\]/g, (_, m: string) => `\n$$\n${m.trim()}\n$$\n`)
            .replace(/\\\(([\s\S]+?)\\\)/g, (_, m: string) => `$$${m.trim()}$$`),
    )
    .join('')
