import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

// The OS "reduce motion" setting, live. global.css already stops CSS animations
// under it; script-driven motion (Web Animations, rAF) has to check this itself.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia(QUERY).matches)
  useEffect(() => {
    const mq = window.matchMedia(QUERY)
    const on = () => setReduced(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return reduced
}
