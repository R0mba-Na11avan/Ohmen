import { useEffect, useRef, useState } from 'react'
import { useReducedMotion } from './useReducedMotion'

// Tokens arrive in uneven bursts (a subscription CLI may hand over a whole
// paragraph at once); this lets a reply out at a steady typing pace instead.
// The pace scales with the backlog, so a big burst still finishes in a couple
// of seconds, and the reveal keeps typing after the stream ends until it has
// caught up. A reply that was never live (reloaded from history) shows at once,
// and so does everything under reduced motion or while the window is hidden
// (minimised or covered: no frames are drawn then, so a reveal would stall).
const BASE_CPS = 110 // characters per second once the backlog is small
const TAU = 450 // ms: a backlog drains with this time constant
const COMMIT_MS = 30 // re-render (and re-parse the markdown) at most this often

function useDocumentHidden(): boolean {
  const [hidden, setHidden] = useState(() => document.hidden)
  useEffect(() => {
    const on = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return hidden
}

/** The part of `target` to show, and whether it is still typing. */
export function useSteadyText(target: string, live: boolean): { text: string; typing: boolean } {
  const reduced = useReducedMotion()
  const hidden = useDocumentHidden()
  // Animate only a reply this view saw streaming.
  const [animate, setAnimate] = useState(live)
  if (live && !animate) setAnimate(true)
  if (!live && animate && hidden) setAnimate(false) // ended while nobody watched: done
  const steady = animate && !reduced && !hidden
  const [len, setLen] = useState(() => (steady ? 0 : target.length))
  const s = useRef({ len: steady ? 0 : target.length, target, live, raf: 0, last: 0, commit: 0 })
  s.current.target = target
  s.current.live = live

  useEffect(() => {
    const st = s.current
    if (!steady) {
      st.len = st.target.length
      setLen(st.len)
      return
    }
    const tick = (now: number) => {
      const dt = st.last ? Math.min(now - st.last, 64) : 16
      st.last = now
      const goal = st.target.length
      if (st.len > goal) st.len = goal
      if (goal > st.len) {
        const backlog = goal - st.len
        const step = Math.max((BASE_CPS * dt) / 1000, backlog * (1 - Math.exp(-dt / TAU)))
        let next = Math.min(goal, st.len + Math.ceil(step))
        const c = st.target.charCodeAt(next)
        if (next < goal && c >= 0xdc00 && c <= 0xdfff) next++ // never split a surrogate pair
        st.len = next
        if (now - st.commit >= COMMIT_MS || next === goal) {
          st.commit = now
          setLen(next)
        }
      } else if (!st.live) {
        setAnimate(false) // caught up after the stream ended: done typing
        return
      }
      st.raf = requestAnimationFrame(tick)
    }
    st.raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(st.raf)
      st.last = 0
    }
  }, [steady])

  if (!steady) return { text: target, typing: false }
  return { text: target.slice(0, len), typing: true }
}
