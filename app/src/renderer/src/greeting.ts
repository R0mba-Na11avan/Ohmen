// The new-chat headline: a line for the time of day, picked once per new chat.
// `{project}` lines name the open KiCad project and are skipped when there is none.
export type Bucket = 'lateNight' | 'morning' | 'afternoon' | 'evening' | 'night'

export const bucketOf = (hour: number): Bucket =>
  hour < 5 ? 'lateNight' : hour < 12 ? 'morning' : hour < 17 ? 'afternoon' : hour < 22 ? 'evening' : 'night'

const LINES: Record<Bucket, string[]> = {
  lateNight: [
    'Burning the midnight copper?',
    'Still up? Ohmen is too.',
    'Quiet bus, quiet house. What are we tracing?',
    'Late-night respin of {project}?',
    'Low-traffic hours. Good time to check the ground plane.',
  ],
  morning: [
    'Morning. Coffee, then ERC?',
    'Morning. {project} is right where you left it.',
    'Fresh eyes, fresh netlist.',
    'Morning. Ohmen has the datasheets open.',
    'Rise and route.',
  ],
  afternoon: [
    "Afternoon. What's on the bench?",
    'Afternoon. Shall we look over {project}?',
    'Ohmen is ready for the fine print.',
    'Post-lunch, pre-fab. What needs checking?',
    "Any pins you don't trust?",
  ],
  evening: [
    'Evening. One more pass on {project}?',
    'Evening. Time to double-check the decoupling caps.',
    'Evening thoughts on a schematic?',
    'Winding down, or winding an inductor?',
    'What should we verify before you order boards?',
  ],
  night: [
    'Working late on {project}?',
    'A good night for a design review.',
    "Night shift on the bench. What's up?",
    'Ohmen is still powered. What are we probing?',
    'Late, but the rails still read 3.3 V.',
  ],
}

export function pickGreeting(date: Date, projectName: string | null, rand: () => number = Math.random): string {
  const pool = LINES[bucketOf(date.getHours())].filter((l) => projectName || !l.includes('{project}'))
  const line = pool[Math.min(pool.length - 1, Math.floor(rand() * pool.length))]
  return line.replace('{project}', projectName ?? '')
}
