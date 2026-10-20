import { defineConfig } from 'electron-vite'
import type { Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// What the built renderer may load: its own files, the loopback sidecar (HTTP +
// the chat WebSocket), and remote images (part photos). No remote scripts, no
// eval, no plugins. Build only: the dev server needs inline HMR scripts.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'", // React and mermaid set inline styles
  "img-src 'self' data: blob: https: http://127.0.0.1:*",
  "font-src 'self' data:",
  "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*",
  "object-src 'none'",
  "frame-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ')

function contentSecurityPolicy(): Plugin {
  return {
    name: 'ohmen-csp',
    apply: 'build',
    transformIndexHtml: () => [
      { tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP }, injectTo: 'head-prepend' },
    ],
  }
}

// electron-vite defaults: main = src/main/index.ts, preload = src/preload/index.ts,
// renderer root = src/renderer (index.html + src/). Output goes to out/.
export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    plugins: [react(), contentSecurityPolicy()],
  },
})
