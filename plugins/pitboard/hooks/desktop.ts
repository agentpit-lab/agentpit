import type { Level } from './view'

// Desktop gauges as plain-image Svgs: an interactive Svg is a sandboxed frame that reloads, and
// so flashes, on every change of its markup; an image swaps in place. Words are the surface's
// own Text.

export type Piece = { source: string; width: number; height: number; alt: string }

// What a colour means, on every surface (desktop row and cards, terminal row and detail line, pane),
// so a reader learns it once. A value gets a colour only when it carries one of these meanings:
//   none (grey)  calm: nothing to do. Good values stay uncoloured too; colour is kept for attention.
//   yellow       watch: worth a glance, not a problem (a pace over budget, a week ahead of even pace).
//   red          act: changes what to do next (a limit that runs out before its reset, context near
//                compaction, a week more than a day ahead of even pace).
//   blue         a cold cache: lost, not urgent, kept apart from the limit warnings. The terminal
//                draws it in the theme's teal (planMode), its nearest cool key.
//   green        a state that is on and fine (auto-compact on). Never "a good value".
// Model and effort colours in the info card name the model; they are identity, not state.
// Watch and act share one colour in both themes, so the native Text beside a gauge (which cannot
// follow the colour scheme) matches it; only the calm gauge and the track adapt.
export const DESKTOP_LEVEL: Record<Level, string> = { calm: '#8a8a85', watch: '#c4841d', act: '#e0483e' }
// A cold cache is lost, not urgent: its own cool colour keeps it apart from the limit warnings.
export const DESKTOP_COLD = '#2a9db5'
// Auto-compact on: a calm state, so a green that no warning uses; one shade for both themes.
export const DESKTOP_ON = '#3d9a5c'

// A card value's colour: a level past calm, or `on` for a state that is on and fine.
export type Tone = Level | 'on'

const STYLE = `
  .disc{fill:#dcdbd6}
  .rule{fill:#c9c8c3}
  .tick{fill:#2f2f2c}
  .fill-calm{fill:${DESKTOP_LEVEL.calm}}
  .fill-watch{fill:${DESKTOP_LEVEL.watch}}
  .fill-act{fill:${DESKTOP_LEVEL.act}}
  @media (prefers-color-scheme: dark){
    .disc{fill:#3a3a37}
    .rule{fill:#55554f}
    .tick{fill:#ecebe7}
    .fill-calm{fill:#9a9893}
  }`

const doc = (w: number, h: number, body: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><style>${STYLE}</style>${body}</svg>`

// Whole percent steps: a countdown gauge then changes its image at most once per step.
const clampPct = (pct: number) => Math.round(Math.max(0, Math.min(100, pct)))

// A filled pie the size of the surface's own context ring: a light disc, the used share as a
// sector from twelve o'clock, clockwise.
export function pieSvg(pct: number, level: Level, size = 16): Piece {
  const p = clampPct(pct)
  const r = size / 2 - 0.5
  const mid = size / 2
  let sector = ''
  if (p >= 100) sector = `<circle class="fill-${level}" cx="${mid}" cy="${mid}" r="${r}"/>`
  else if (p > 0) {
    const a = (p / 100) * 2 * Math.PI
    const x = (mid + r * Math.sin(a)).toFixed(2)
    const y = (mid - r * Math.cos(a)).toFixed(2)
    sector = `<path class="fill-${level}" d="M${mid} ${mid} L${mid} ${(mid - r).toFixed(2)} A${r} ${r} 0 ${a > Math.PI ? 1 : 0} 1 ${x} ${y} Z"/>`
  }
  const body = `<circle class="disc" cx="${mid}" cy="${mid}" r="${r}"/>${sector}`
  return { source: doc(size, size, body), width: size, height: size, alt: `${p}%` }
}

// A limit as a bar with a tick where even pace would stand now: the share of the window gone.
// Used past the tick means burning faster than the reset allows.
export function barSvg(pct: number, level: Level, pace: number | null, width = 40): Piece {
  const p = clampPct(pct)
  const fill = (p / 100) * width
  let body = `<rect class="disc" x="0" y="4" width="${width}" height="4" rx="2"/>`
  if (p > 0) body += `<rect class="fill-${level}" x="0" y="4" width="${fill.toFixed(1)}" height="4" rx="2"/>`
  if (pace !== null) {
    const x = Math.max(1, Math.min(width - 1, (clampPct(pace) / 100) * width))
    body += `<rect class="tick" x="${(x - 1).toFixed(1)}" y="1" width="2" height="10" rx="1"/>`
  }
  return { source: doc(width, 12, body), width, height: 12, alt: pace === null ? `${p}%` : `${p}%, even pace ${clampPct(pace)}%` }
}

// The thin rule between groups of readings; the image is wider than the line, which a surface
// may drop at one pixel.
export function ruleSvg(height = 14, width = 9): Piece {
  const x = Math.floor(width / 2)
  return { source: doc(width, height, `<rect class="rule" x="${x}" y="0" width="1" height="${height}"/>`), width, height, alt: '|' }
}

// desktopBullets: what only the desktop card says (its own context ring), after the bullets, two at most.
// bar: a limit drawn the card's full width: used, where the current pace leads by the reset (paler),
// and the even-pace tick.
export type CardBar = { used: number; projected?: number; tick: number | null; level: Level }
// advice: the first bullet when the reading needs a decision, also kept apart for the popover.
export type Card = { title: string; level?: Level; rows: [string, string, Tone?][]; bullets: string[]; advice?: string; desktopBullets?: string[]; bar?: CardBar }

// A limit's wide bar from y down 6 px: used, the projection paler, the even-pace tick over both.
function barParts(b: CardBar, y: number, w: number): string[] {
  const at = (pct: number) => (Math.max(0, Math.min(100, pct)) / 100) * w
  const parts = [`<rect class="disc" x="0" y="${y}" width="${w}" height="6" rx="3"/>`]
  if (b.projected !== undefined && b.projected > b.used)
    parts.push(`<rect class="fill-${b.level}" fill-opacity="0.35" x="0" y="${y}" width="${at(b.projected).toFixed(1)}" height="6" rx="3"/>`)
  if (b.used > 0) parts.push(`<rect class="fill-${b.level}" x="0" y="${y}" width="${at(b.used).toFixed(1)}" height="6" rx="3"/>`)
  if (b.tick !== null) parts.push(`<rect class="tick" x="${Math.max(0, at(b.tick) - 1).toFixed(1)}" y="${y - 3}" width="2" height="12" rx="1"/>`)
  return parts
}

export function wideBarSvg(b: CardBar, width: number): Piece {
  const alt = [`${Math.round(b.used)}% used`, ...(b.projected !== undefined ? [`~${Math.round(b.projected)}% at reset`] : []), ...(b.tick !== null ? [`even ${Math.round(b.tick)}%`] : [])].join(', ')
  return { source: doc(width, 12, barParts(b, 3, width).join('')), width, height: 12, alt }
}

// A cache state that has no countdown left to draw: a flame while a reply is running, a snowflake
// once the cache has expired. Drawn in place of the pie, the size of it.
export function cacheIconSvg(state: 'hot' | 'cold', size = 16): Piece {
  const body =
    state === 'hot'
      ? `<path fill="${DESKTOP_LEVEL.act}" d="M8 1C9 4 12 5.5 12 9.5A4 4 0 0 1 4 9.5C4 7.5 5 6.5 6 5.5C6 7 6.8 8 7.6 8.2C7 6 7.2 3.5 8 1Z"/>`
      : `<g stroke="${DESKTOP_COLD}" stroke-width="1.4" stroke-linecap="round" fill="none">` +
        `<path d="M8 1.5V14.5M2.4 4.75L13.6 11.25M2.4 11.25L13.6 4.75"/>` +
        `<path d="M6.3 2.6L8 4L9.7 2.6M6.3 13.4L8 12L9.7 13.4"/></g>`
  return { source: doc(size, size, body), width: size, height: size, alt: state === 'hot' ? 'hot' : 'cold' }
}
