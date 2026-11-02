import { advisorView, CALM_FILL, cardFor, COLD, desktopModelColor, DIM, displayModel, EFFORT_COLORS, type Gauge, GROUPS, IDLE_MARK, isKnownFamily, LABEL, LEVEL, listOrder, modelColor, ON, PANE_GROUPS, type PaneCard, paneCards, paneNext, paneVerdict, readings, rowFit, RULE, TRACK, VALUE } from './cards'
import { barSvg, cacheIconSvg, type Card, type CardBar, DESKTOP_COLD, DESKTOP_LEVEL, DESKTOP_ON, pieSvg, ringMarksSvg, ruleSvg, type Tone, wideBarSvg } from './desktop'
import type { View } from './view'
import type { PitboardButton } from '../types'

// The element tables differ per surface; these trees use only what all of them share,
// plus Svg where the surface has it.
type Ctor = (props: any) => any
export type Els = { Box: Ctor; Text: Ctor; Svg?: Ctor; Button?: Ctor }

// The pane: a verdict, what happens next, then the cards folded into groups. A press unfolds a card,
// and its explanations one level further. The mobile app's only view.

// Widths in cells of the pane's own body. Under PANE_NARROW the folded lines drop their bars and the
// Next consequences go under their time; Desktop puts the verdict and Next beside the groups from
// PANE_WIDE_DESKTOP, about 880 px. One bar width serves the whole pane.
export const PANE_NARROW = 60
export const PANE_WIDE_DESKTOP = 120
// Under this many cells the folded lines put their values under the label instead of beside it.
export const PANE_STACK = 44
const BAR_MIN = 10
const BAR_MAX = 20
// Cells between a card's label and its value, and after a bar.
const LABEL_GAP = 2
const BAR_GAP = 2
const STACK_INDENT = 2

export type PaneOptions = {
  // Cells across the pane's body, from the region the body is drawn in, else from the pane's own props.
  columns: number
  isRemote: boolean
  // The file name of the scenario the view is drawn from (PITBOARD_SCENARIO): the pane says so on its first line.
  scenario?: string
  // The keys of the unfolded cards, and of the cards whose explanations are open.
  open: string[]
  how: string[]
  onToggle: (key: string) => void
  onHow: (key: string) => void
  onUnfoldAll: () => void
  onHowAll: () => void
}

export const paneBarCells = (columns: number) => Math.max(BAR_MIN, Math.min(BAR_MAX, Math.floor(columns * 0.2)))

const toneColor = (tone?: Tone | 'cold') => (tone === 'on' ? ON : tone === 'cold' ? COLD : tone && tone !== 'calm' ? LEVEL[tone] : VALUE)
// Spaces a surface keeps at the edge of a run: a flex item's trailing blank would collapse.
const NBSP = '\u00a0'
const NBSP_DOT = `${NBSP}·${NBSP}`
// Values filled into lines of at most `width` cells, a value never split; a lone long value keeps its line.
function valueLines<T extends { text: string }>(values: T[], width: number): T[][] {
  const lines: T[][] = []
  let used = 0
  for (const v of values) {
    const w = [...v.text].length
    const last = lines[lines.length - 1]
    if (last && used + 3 + w <= width) {
      last.push(v)
      used += 3 + w
    } else {
      lines.push([v])
      used = w
    }
  }
  return lines
}
const caret = (isOpen: boolean) => (isOpen ? '⌄' : '›')

// A bar in cells: used █ in the level's colour, the forecast ▒, the time tick │ in a cell of its own,
// the rest ░. Where the surface has Svg the same bar is an image.
function paneBar(els: Els, b: CardBar, cells: number, key: string) {
  const { Box, Text, Svg } = els
  if (Svg) {
    const bar = wideBarSvg(b, cells * 8)
    return <Svg key={key} source={bar.source} alt={bar.alt} width={bar.width} height={bar.height} />
  }
  const at = (pct: number) => Math.round((Math.max(0, Math.min(100, pct)) / 100) * cells)
  // A value above zero keeps at least one cell, so 2% of 16 cells still shows; so does a forecast above the use.
  const used = b.used > 0 ? Math.max(1, at(b.used)) : 0
  const projected = b.projected !== undefined && b.projected > b.used ? Math.max(used + 1, at(b.projected)) : used
  const tick = b.tick === null ? -1 : Math.min(cells - 1, at(b.tick))
  const usedColor = b.level === 'calm' ? VALUE : LEVEL[b.level]
  const runs: { glyph: string; color: string; n: number }[] = []
  for (let i = 0; i < cells; i++) {
    const [glyph, color] = i === tick ? ['│', VALUE] : i < used ? ['█', usedColor] : i < projected ? ['▒', DIM] : ['░', TRACK]
    const last = runs[runs.length - 1]
    if (last && last.glyph === glyph && last.color === color) last.n++
    else runs.push({ glyph: glyph!, color: color!, n: 1 })
  }
  return (
    <Box key={key} flexShrink={0}>
      <Text>
        {runs.map((r, i) => (
          <Text key={String(i)} color={r.color}>
            {r.glyph.repeat(r.n)}
          </Text>
        ))}
      </Text>
    </Box>
  )
}

export function drawPane(els: Els, v: View, o: PaneOptions) {
  const { Box, Text, Button } = els
  const cards = paneCards(v, o.isRemote)
  const verdict = paneVerdict(v)
  const next = paneNext(v)
  const isNarrow = o.columns < PANE_NARROW
  const isWide = o.isRemote && o.columns >= PANE_WIDE_DESKTOP
  const barCells = paneBarCells(o.columns)
  // The groups' own width: in the wide layout they stand beside the verdict and Next.
  const groupColumns = isWide ? o.columns - Math.floor(o.columns * 0.4) - 4 : o.columns
  // A label with a colour of its own (the Session card's model) keeps the caret dim and draws the name in it;
  // without Button the plain Text takes the colour whole.
  const press = (label: string, key: string, onPress: () => void, name?: { caret: string; text: string; color: string }) =>
    Button ? (
      name ? (
        <Button key={key} label={label} plain dimColor onPress={onPress}>
          {name.caret} <Text color={name.color}>{name.text}</Text>
        </Button>
      ) : (
        <Button key={key} label={label} plain dimColor onPress={onPress} />
      )
    ) : (
      <Text key={key} color={name?.color ?? DIM}>
        {label}
      </Text>
    )

  const verdictBlock = (
    <Box key="verdict" flexDirection="column" marginBottom={1}>
      <Text bold color={verdict.level === 'calm' ? VALUE : LEVEL[verdict.level]} wrap="wrap">
        {verdict.sentence}
      </Text>
      {verdict.advice.map((a, i) => (
        <Text key={`advice-${i}`} color={VALUE} wrap="wrap">
          {a}
        </Text>
      ))}
      {verdict.forecast && verdict.forecast.level !== 'calm' ? (
        <Text key="forecast" color={LEVEL[verdict.forecast.level]} wrap="wrap">
          {verdict.forecast.text}
        </Text>
      ) : null}
    </Box>
  )

  const whenWidth = Math.max(0, ...next.map(e => e.when.length)) + 2
  const nextBlock = next.length ? (
    <Box key="next" flexDirection="column" marginBottom={1}>
      <Text bold color={LABEL}>
        Next
      </Text>
      {next.map((e, i) => (
        <Box key={`next-${i}`} flexDirection="row">
          <Box width={whenWidth} flexShrink={0}>
            <Text color={LEVEL[e.level]}>{e.when}</Text>
          </Box>
          {isNarrow ? (
            <Box flexDirection="column" flexGrow={1} flexShrink={1}>
              <Text bold color={e.level === 'calm' ? VALUE : LEVEL[e.level]} wrap="wrap">
                {e.what}
              </Text>
              {e.why ? (
                <Text color={DIM} wrap="wrap">
                  {e.why}
                </Text>
              ) : null}
            </Box>
          ) : (
            <Box flexGrow={1} flexShrink={1}>
              <Text wrap="wrap">
                <Text bold color={e.level === 'calm' ? VALUE : LEVEL[e.level]}>
                  {e.what}
                </Text>
                {e.why ? <Text color={DIM}>{`${NBSP}${NBSP}${e.why}`}</Text> : null}
              </Text>
            </Box>
          )}
        </Box>
      ))}
    </Box>
  ) : null

  // The label column holds the caret, the label and the mark; one width for every card.
  const labelWidth = Math.max(0, ...cards.map(c => caret(true).length + 1 + c.label.length + (c.mark ? 2 : 0))) + LABEL_GAP
  const hasBars = !isNarrow && cards.some(c => c.bar)
  // Unfolded rows of every card share one value column, so cards opened together line up; at least the
  // folded value column, and per card only when the shared one would take more than half a narrow pane.
  const rowWidth = (c: PaneCard) => Math.max(0, ...listOrder(c.rows).map(([l]) => l.length)) + LABEL_GAP
  const sharedRowLabel = Math.max(labelWidth - 2, ...cards.map(rowWidth))
  const unfolded = (c: PaneCard) => {
    const rows = listOrder(c.rows)
    const rowLabel = sharedRowLabel <= groupColumns / 2 ? sharedRowLabel : rowWidth(c)
    const isHow = o.how.includes(c.key)
    return (
      <Box key={`${c.key}-open`} flexDirection="column" paddingLeft={2} marginBottom={1}>
        {rows.map(([l, val, tone], i) => (
          <Box key={`${c.key}-r${i}`} flexDirection="row">
            <Box width={rowLabel} flexShrink={0}>
              <Text color={LABEL}>{l}</Text>
            </Box>
            <Box flexGrow={1} flexShrink={1}>
              <Text color={toneColor(tone)} wrap="wrap">
                {val}
              </Text>
            </Box>
          </Box>
        ))}
        {/* The folded line already shows the bar unless the pane is narrow; here it sits in the value column. */}
        {c.bar && !hasBars ? (
          <Box key={`${c.key}-bar`} paddingLeft={rowLabel}>
            {paneBar(els, c.bar, barCells, `${c.key}-barimg`)}
          </Box>
        ) : null}
        {c.advice ? (
          <Text key={`${c.key}-advice`} color={VALUE} wrap="wrap">
            {c.advice}
          </Text>
        ) : null}
        {c.explain.length ? press(`${caret(isHow)} How it is computed`, `how-${c.key}`, () => o.onHow(c.key)) : null}
        {isHow
          ? c.explain.map((b, i) => (
              <Box key={`${c.key}-b${i}`} flexDirection="row" alignItems="flex-start" paddingLeft={2}>
                <Text color={DIM}>{'• '}</Text>
                <Box flexShrink={1}>
                  <Text color={DIM} wrap="wrap">
                    {b}
                  </Text>
                </Box>
              </Box>
            ))
          : null}
      </Box>
    )
  }
  const folded = (c: PaneCard) => {
    const isOpen = o.open.includes(c.key)
    const isStacked = o.columns < PANE_STACK
    const markColor = c.markTone === 'on' ? ON : c.markTone ? LEVEL[c.markTone] : DIM
    const label = (
      <Box key={`${c.key}-label`} width={isStacked ? undefined : labelWidth} flexShrink={0} flexDirection="row" columnGap={1}>
        <Box flexShrink={0}>{press(`${caret(isOpen)} ${c.label}`, `card-${c.key}`, () => o.onToggle(c.key), c.labelColor ? { caret: caret(isOpen), text: c.label, color: c.labelColor } : undefined)}</Box>
        {c.mark ? <Text color={markColor}>{c.mark}</Text> : null}
      </Box>
    )
    // A line with no bar starts its values where the bars start. Two cells stay free at the right edge.
    const barBox = hasBars && c.bar
    const used = isStacked ? STACK_INDENT : labelWidth + (barBox ? barCells + BAR_GAP : 0)
    const width = Math.max(8, groupColumns - used - 2)
    // A line breaks only between its values: the lines are filled here, the separator stays inside a
    // line, and a continuation stands under the first value. A value alone wider than the line wraps on its words.
    const valuesBlock = (
      <Box key={`${c.key}-values`} flexDirection="column" flexGrow={1} flexShrink={1}>
        {valueLines(c.values, width).map((line, li) => (
          <Box key={`${c.key}-l${li}`} flexDirection="row">
            {line.map((val, i) => (
              <Box key={`${c.key}-v${li}-${i}`} flexShrink={line.length === 1 ? 1 : 0}>
                {i > 0 ? <Text color={RULE}>{NBSP_DOT}</Text> : null}
                <Text color={val.color ?? toneColor(val.tone)} wrap="wrap">
                  {val.text}
                </Text>
              </Box>
            ))}
          </Box>
        ))}
      </Box>
    )
    return (
      <Box key={c.key} flexDirection="column">
        {isStacked ? (
          <Box flexDirection="column">
            {label}
            <Box paddingLeft={STACK_INDENT}>{valuesBlock}</Box>
          </Box>
        ) : (
          <Box flexDirection="row">
            {label}
            {barBox && c.bar ? <Box width={barCells + BAR_GAP} flexShrink={0}>{paneBar(els, c.bar, barCells, `${c.key}-fold-bar`)}</Box> : null}
            {valuesBlock}
          </Box>
        )}
        {isOpen ? unfolded(c) : null}
      </Box>
    )
  }
  const groupBlocks = PANE_GROUPS.map(g => ({ g, cs: cards.filter(c => c.group === g.id) }))
    .filter(x => x.cs.length)
    .map(({ g, cs }) => (
      <Box key={g.id} flexDirection="column" marginBottom={1}>
        <Text bold color={LABEL}>
          {g.title}
        </Text>
        {cs.map(folded)}
      </Box>
    ))
  const isAllOpen = cards.every(c => o.open.includes(c.key))
  const isAllHow = cards.every(c => o.how.includes(c.key) || c.explain.length === 0)
  const buttons = (
    <Box key="buttons" flexDirection="row" flexWrap="wrap" columnGap={2}>
      {press(isAllOpen ? 'Fold all' : 'Unfold all', 'unfold-all', o.onUnfoldAll)}
      {press(isAllOpen && isAllHow ? 'Hide the explanations' : 'How the numbers work', 'how-all', o.onHowAll)}
    </Box>
  )
  const banner = o.scenario ? (
    <Text key="scenario" color={toneColor('watch')} wrap="wrap">
      Scenario: {o.scenario}
    </Text>
  ) : null
  const body = isWide ? (
    <Box flexDirection="row" columnGap={4}>
      <Box flexDirection="column" width={Math.floor(o.columns * 0.4)} flexShrink={0}>
        {verdictBlock}
        {nextBlock}
      </Box>
      <Box flexDirection="column" flexGrow={1} flexShrink={1}>
        {groupBlocks}
        {buttons}
      </Box>
    </Box>
  ) : (
    <Box flexDirection="column">
      {verdictBlock}
      {nextBlock}
      {groupBlocks}
      {buttons}
    </Box>
  )
  // The scenario line stands above whichever layout is drawn; without a scenario the body is the whole pane.
  return banner ? (
    <Box flexDirection="column">
      {banner}
      {body}
    </Box>
  ) : (
    body
  )
}

// Desktop: one row of filled pies the size of the surface's own context ring. A calm pie is grey
// and silent; past a threshold it takes the level's colour and shows one number. A click on a
// reading's name gives it a detail card, shown while the pointer is over it; another click
// switches or removes it. Cards on every reading were dropped: desktop keeps a revealed card up
// after the pointer leaves, so several showed at once. Readings sit in groups (context | cache | limits) split by a thin rule.
// `opens` names the detail a label opens when two labels share one (TTL and Hit open the cache).

// Every reading carries its card, shown while the pointer is over the reading. The redraw every
// second used to keep desktop from noticing the pointer leave, so cards stayed up; with the row
// redrawn only on a change, hover alone is tried again.
const HOVER_CARDS = true
// Desktop frames the popover at about 420 px and clips past it; its text runs wider than a cell a
// character. A bar or wrapped advice takes at most this many cells.
const NATIVE_CARD_CELLS = 44
// The narrowest a card gets, and desktop cells a character of its text takes (estimated from screenshots).
const NATIVE_CARD_MIN_CELLS = 26
// 1.15 made the bar, and so the card, wider than its text; 0.9 follows the text.
const NATIVE_CELLS_PER_CHAR = 0.9
// A faint grey wash under the hovered reading: a border takes a whole cell on each side and wraps
// the row, a background takes no room. Half-transparent grey shades a light band and lifts a dark one.
const HOVER_FRAME = { backgroundColor: '#8080801f' }


export function drawDesktop(els: Els, v: View, open: string | null, toggle: (key: string) => void, columns = Infinity, isPaneOpen = false) {
  const { Box, Button, Svg, Text } = els
  const { gauges, info, advisor, advisorOn } = readings(v)
  const { style, info: showInfo } = rowFit(v, columns)
  const bars = style !== 'pies'
  const name = (key: string, label: string) =>
    HOVER_CARDS ? (
      <Text key={`n-${key}`} dimColor>
        {label}
      </Text>
    ) : Button ? (
      <Button key={`b-${key}`} label={label} plain dimColor={open !== key} onPress={() => toggle(key)} />
    ) : (
      <Text dimColor>{label}</Text>
    )
  // Without Svg the marks are glyphs before the value; with it they share the pie's image.
  const glyphMarks = (g: Gauge) => (g.mark && !Svg ? [<Text key={`m-${g.key}`} color={DESKTOP_ON}>{markText(g)}</Text>] : [])
  const content = (g: Gauge) => {
    if (bars && g.pace !== undefined) {
      const bar = barSvg(g.pct ?? 0, g.barLevel ?? g.level, g.pace, undefined, g.paceName, g.projected !== undefined ? { projected: g.projected, projectedName: g.projectedName } : undefined)
      return [
        name(g.key, g.label),
        Svg ? <Svg key={`bar-${g.key}`} source={bar.source} alt={`${g.label} ${g.barAlt ?? bar.alt}`} width={bar.width} height={bar.height} /> : null,
        g.value ? (
          <Text key={`v-${g.key}`} bold color={g.level === 'calm' ? undefined : DESKTOP_LEVEL[g.level]}>
            {g.value}
          </Text>
        ) : null,
        style === 'bars' && g.reset ? (
          <Text key={`r-${g.key}`} dimColor>
            {g.reset}
          </Text>
        ) : null,
      ]
    }
    const ring = g.icon
      ? cacheIconSvg(g.icon)
      : g.mark && Svg
        ? ringMarksSvg(g.pct === null ? null : { pct: g.pct, level: g.level }, !!g.idle)
        : g.pct === null
          ? null
          : pieSvg(g.pct, g.barLevel ?? g.level)
    return [
      name(g.key, g.label),
      ring && Svg ? <Svg key={`pie-${g.key}`} source={ring.source} alt={`${g.label} ${g.icon || g.mark ? ring.alt : (g.value ?? 'n/a')}`} width={ring.width} height={ring.height} /> : null,
      ...glyphMarks(g),
      g.level !== 'calm' && g.value && !g.icon ? (
        <Text key={`v-${g.key}`} bold color={g.tone === 'cold' ? DESKTOP_COLD : DESKTOP_LEVEL[g.level]}>
          {g.value}
        </Text>
      ) : null,
      g.extra ? (
        <Text key={`x-${g.key}`} dimColor>
          {g.extra}
        </Text>
      ) : null,
      g.level !== 'calm' && g.reset ? (
        <Text key={`r-${g.key}`} dimColor>
          {g.reset}
        </Text>
      ) : null,
    ]
  }
  // A reading that opens another's card (Hit opens the cache's) is drawn inside that reading's Box,
  // so the two are one hover area with one card. The frame shows which reading the card belongs to.
  const reading = (g: Gauge) => (
    <Box key={g.key} position="relative" flexDirection="row" alignItems="center" columnGap={1} paddingX={1} flexShrink={0} hover={HOVER_FRAME}>
      {[g, ...gauges.filter(x => x.opens === g.key)].flatMap(content)}
      {HOVER_CARDS || open === g.key ? popover(cardFor(gauges, info, g.key) ?? g.card) : null}
    </Box>
  )
  // Only the opened reading has a card, revealed by the surface's hover popover (the only element
  // desktop frames and lets past the band's edge): one card at most, pointing at its reading.
  // The open pane holds every card already; a card over the row would only repeat it.
  const popover = (card: Card) => (isPaneOpen ? null : nativeCard(card))
  // The card in desktop's own Box and Text: its font at its size, laid out by desktop, so nothing is
  // scaled. A title, one reading to a line with the value right-aligned, a limit's bar, and advice.
  const toneOf = (tone?: Tone) => (tone === 'on' ? DESKTOP_ON : tone && tone !== 'calm' ? DESKTOP_LEVEL[tone] : undefined)
  const nativeCard = (card: Card) => {
    // One reading to a line in the list order the pane uses: desktop's popover is about 420 px wide
    // and clips what runs past it, and one column always fits.
    const rows = listOrder(card.rows)
    const single = { label: Math.max(0, ...rows.map(([l]) => l.length)), value: Math.max(0, ...rows.map(([, val]) => val.length)) }
    // A line spans the card, its value at the right edge, in line with the end of a bar or advice.
    const lines = rows.map(([l, val, tone], i) => (
      <Box key={`s${i}`} flexDirection="row">
        <Box minWidth={single.label + 2} flexGrow={1} flexShrink={0}>
          <Text dimColor>{l}</Text>
        </Box>
        <Box minWidth={single.value} flexShrink={0} justifyContent="flex-end">
          <Text bold color={toneOf(tone)}>
            {val}
          </Text>
        </Box>
      </Box>
    ))
    // The bar and the advice take the readings' width, so the popover stays as narrow as they are.
    const cellsWide = Math.min(NATIVE_CARD_CELLS, Math.max(NATIVE_CARD_MIN_CELLS, Math.round((single.label + 2 + single.value) * NATIVE_CELLS_PER_CHAR)))
    const bar = card.bar ? wideBarSvg(card.bar, cellsWide * 8) : null
    // Explanations live in the pane; a card says only what to do when something needs it.
    const bullets = card.advice ? [card.advice] : []
    return (
      <Box position="absolute" top={0} left={0} display="none" hover={{ display: 'flex' }}>
        <Box flexDirection="column">
          <Text bold color={card.level && card.level !== 'calm' ? DESKTOP_LEVEL[card.level] : undefined}>
            {card.title}
          </Text>
          {lines}
          {bar && Svg ? <Svg source={bar.source} alt={bar.alt} width={bar.width} height={bar.height} /> : null}
          {bullets.map((b, n) => (
            <Box key={`b${n}`} width={cellsWide}>
              <Text dimColor wrap="wrap">
                {b}
              </Text>
            </Box>
          ))}
        </Box>
      </Box>
    )
  }
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  const rule = ruleSvg()
  // The advisor sits between context and cache, as on the terminal; model and effort are the
  // surface's own, under the prompt.
  const adv = advisorView(advisorOn)
  // Shown only while an advisor is set: off is the usual state and stays in ⓘ.
  const advisorGroup = v.meta?.advisor ? (
    <Box key="group-advisor" position="relative" flexDirection="row" alignItems="center" columnGap={1} paddingX={1} flexShrink={0} hover={HOVER_FRAME}>
      <Text dimColor>Advisor</Text>
      {adv.name ? (
        <Text color={desktopModelColor(adv.name)} dimColor={adv.isInactive} italic={!isKnownFamily(adv.name)} strikethrough={adv.isInactive}>
          {adv.name}
        </Text>
      ) : (
        <Text dimColor>off</Text>
      )}
      {adv.isInactive ? <Text dimColor>(inactive)</Text> : null}
      {advisor ? popover(advisor) : null}
    </Box>
  ) : null
  const parts = groups.map(gs => (
    <Box key={`group-${gs[0]?.group}`} flexDirection="row" alignItems="center" flexShrink={0}>
      {gs
        .filter(g => !g.opens)
        .flatMap((g, i) => [
          ...(i > 0 ? [<Text key={`dot-${g.key}`} dimColor>·</Text>] : []),
          reading(g),
        ])}
    </Box>
  ))
  if (advisorGroup) parts.splice(groups[0]?.[0]?.group === 'context' ? 1 : 0, 0, advisorGroup)
  return (
    <Box flexDirection="column">
      {/* No gaps between parts: each reading's own padding spaces it and widens its hover wash. */}
      <Box flexDirection="row" flexWrap="wrap" alignItems="center">
        {parts.flatMap((part, i) => [
          ...(i > 0 && Svg ? [<Svg key={`rule-${i}`} source={rule.source} alt={rule.alt} width={rule.width} height={rule.height} />] : []),
          part,
        ])}
        {showInfo && Svg ? <Svg key="rule-info" source={rule.source} alt={rule.alt} width={rule.width} height={rule.height} /> : null}
        {showInfo ? (
          <Box key="info" position="relative" flexShrink={0} paddingX={1} hover={HOVER_FRAME}>
            {name('info', 'ⓘ')}
            {HOVER_CARDS || open === 'info' ? popover(info) : null}
          </Box>
        ) : null}
      </Box>
    </Box>
  )
}

// Terminal: the same row in cells. A gauge is two cells of a block that rises in eighths over a
// grey track: a whole cell tall, where a font draws pie glyphs (◔ ◑ ◕) small. In the level's
// colour past a threshold; a click (or Enter under the focus) on a name opens its detail as one
// line under the row.
const EIGHTHS = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
const gaugeCells = (pct: number) => (EIGHTHS[Math.round(Math.max(0, Math.min(100, pct)) / 12.5)] ?? ' ').repeat(2)
// The cache icons, two cells like a gauge: the flame is a wide glyph, the snowflake a narrow one.
const ICON_CELLS = { hot: '🔥', cold: '❄ ' }
const shownValue = (g: Gauge) => g.level !== 'calm' && g.value !== null && !g.icon

// The pane button beside ⓘ, drawn bracketed as the row's one action, apart from the plain names that
// open a card's line.
const PANE_BUTTON = 'PitBoard'

// The state glyphs drawn between a reading's gauge and its value: ↻, and ᶻ (one cell) while idle compaction is expected.
const markText = (g: Gauge) => `${g.mark ?? ''}${g.idle ? IDLE_MARK : ''}`

// The terminal row's width in cells, from the same pieces drawTerminal draws, so the rule above spans
// the row alone. Gaps: 1 inside a reading, 1 + · + 1 between readings, 1 + │ + 1 between groups; the
// last part is ⓘ (2), 1 + · + 1 and the pane button `[ PitBoard ]`, then for each button another plugin
// added a gap, `|`, a gap and its `[ label ]`; the rule runs 1 past it so the last button sits as far
// from its end as ⓘ from the │ before it.
export function terminalCells(v: View, buttons: PitboardButton[] = []): number {
  const { gauges } = readings(v)
  const { advisorOn } = readings(v)
  const readingWidth = (g: Gauge) =>
    [g.label.length, g.pct !== null || g.icon ? 2 : null, g.mark ? markText(g).length : null, shownValue(g) && g.value ? g.value.length : null, g.extra?.length ?? null]
      .filter((n): n is number => n !== null)
      .reduce((sum, n, i) => sum + n + (i > 0 ? 1 : 0), 0)
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  // Readings within a group: gap, dot, gap.
  const groupWidths = groups.map(gs => gs.reduce((sum, g, i) => sum + readingWidth(g) + (i > 0 ? 3 : 0), 0))
  if (v.meta?.advisor) {
    const adv = advisorView(advisorOn)
    groupWidths.push(['Advisor', adv.name ?? 'off', ...(adv.isInactive ? ['(inactive)'] : [])].reduce((sum, w, i) => sum + w.length + (i > 0 ? 1 : 0), 0))
  }
  return [...groupWidths, 2 + 3 + PANE_BUTTON.length + 4 + buttons.reduce((sum, b) => sum + 3 + b.label.length + 4, 0)].reduce((sum, w, i) => sum + w + (i > 0 ? 3 : 0), 0) + 1
}

export function drawTerminal(els: Els, v: View, open: string | null, toggle: (key: string) => void, togglePane: () => void = () => {}, buttons: PitboardButton[] = []) {
  const { Box, Button, Text } = els
  const read = readings(v)
  const { gauges, info, advisor, advisorOn } = read
  const m = v.meta
  const model = m ? displayModel(m.model) : null
  const name = (key: string, label: string) =>
    Button ? (
      <Button key={`b-${key}`} label={label} plain dimColor={open !== key} onPress={() => toggle(key)} />
    ) : (
      <Text dimColor>{label}</Text>
    )
  // While a detail line is open every block gets a second row, blank except a ▲ under the opened
  // label, so the layout itself keeps the mark aligned whatever sits to its left.
  const pointer = (key: string, offset: number) =>
    open === null ? null : (
      <Text key="pointer" color={VALUE}>
        {open === key ? `${' '.repeat(offset)}▲` : ' '}
      </Text>
    )
  const reading = (g: Gauge) => (
    <Box key={g.key} flexDirection="column" flexShrink={0}>
    <Box flexDirection="row" columnGap={1}>
      {name(g.key, g.label)}
      {g.icon ? (
        <Text color={g.icon === 'cold' ? COLD : LEVEL.act}>{ICON_CELLS[g.icon]}</Text>
      ) : g.pct !== null ? (
        <Text color={(g.barLevel ?? g.level) === 'calm' ? CALM_FILL : LEVEL[g.barLevel ?? g.level]} backgroundColor={TRACK}>
          {gaugeCells(g.pct)}
        </Text>
      ) : null}
      {g.mark ? <Text color={ON}>{markText(g)}</Text> : null}
      {shownValue(g) ? (
        <Text bold color={g.tone === 'cold' ? COLD : LEVEL[g.level]}>
          {g.value}
        </Text>
      ) : null}
      {g.extra ? <Text color={LABEL}>{g.extra}</Text> : null}
    </Box>
      {pointer(g.key, Math.floor((g.label.length - 1) / 2))}
    </Box>
  )
  const groups = GROUPS.map(id => gauges.filter(g => g.group === id)).filter(gs => gs.length)
  // Model and effort are needed rarely, so they live in the ⓘ line; the advisor shows in the row
  // while one is set, and in ⓘ always. In ⓘ each takes its own colour, the advisor its model's.
  const { name: advisorName, isInactive } = advisorView(advisorOn)
  const isInfoOf = (key: string) => key === 'info' && m !== null && model !== null
  const INACTIVE_SUFFIX = ' (inactive)'
  const infoColor = (key: string, label: string): { color?: string; dimColor?: boolean; strikethrough?: boolean } => {
    if (!isInfoOf(key) || !m) return {}
    if (label === 'Effort' && m.effort) return { color: EFFORT_COLORS[m.effort] ?? DIM }
    if (label === 'Advisor' && advisorName) return { color: modelColor(advisorName), dimColor: isInactive, strikethrough: isInactive }
    return {}
  }
  const parts = groups.map(gs => (
    <Box key={`group-${gs[0]?.group}`} flexDirection="row" columnGap={1} flexShrink={0}>
      {gs.flatMap((g, i) => [...(i > 0 ? [<Text key={`dot-${g.key}`} color={RULE}>·</Text>] : []), reading(g)])}
    </Box>
  ))
  // Shown only while an advisor is set: off is the usual state and stays in ⓘ.
  if (m?.advisor) {
    parts.splice(
      groups[0]?.[0]?.group === 'context' ? 1 : 0,
      0,
      <Box key="group-advisor" flexDirection="column" flexShrink={0}>
        <Box flexDirection="row" columnGap={1}>
          {name('advisor', 'Advisor')}
          {advisorName ? (
            <Text color={modelColor(advisorName)} dimColor={isInactive} strikethrough={isInactive}>
              {advisorName}
            </Text>
          ) : (
            <Text dimColor>off</Text>
          )}
          {isInactive ? <Text dimColor>(inactive)</Text> : null}
        </Box>
        {pointer('advisor', Math.floor(('Advisor'.length - 1) / 2))}
      </Box>,
    )
  }
  // The session card runs long: from Claude Code on it takes a second line.
  const detailLines = (card: Card) => {
    // A line has no columns to hold open, so the desktop card's empty cells go.
    const rows = listOrder(card.rows)
    const at = rows.findIndex(([label]) => label === 'Claude Code')
    return at > 0 ? [{ rows: rows.slice(0, at) }, { rows: rows.slice(at) }] : [{ rows }]
  }
  // a detail line wraps at the terminal's width, but only between its pairs: the
  // spaces inside a title, a label or a value are no-break, and the break falls after a " · ".
  const glue = (text: string) => text.replace(/ /g, ' ')
  const detail = (key: string) => {
    // The advisor's card exists whenever meta does; with no advisor set its line has no label to hang from.
    const card = key === 'advisor' ? (m?.advisor ? advisor : undefined) : cardFor(gauges, info, key)
    if (!card) return null
    const titleColor = isInfoOf(key) && model ? modelColor(model) : undefined
    return (
      <Box key={`detail-${key}`} flexDirection="column">
        {detailLines(card).map((line, n) => (
          <Text key={`line-${n}`} wrap="wrap">
            {n === 0 ? (
              <Text bold color={titleColor ?? (card.level && card.level !== 'calm' ? LEVEL[card.level] : VALUE)}>
                {glue(card.title)}
              </Text>
            ) : null}
            {line.rows.map(([label, value, level], i) => (
              <Text key={String(i)}>
                <Text color={DIM}>{`${n === 0 || i > 0 ? ' · ' : ''}${glue(label)} `}</Text>
                {isInfoOf(key) && label === 'Advisor' && isInactive && value.endsWith(INACTIVE_SUFFIX) ? (
                  <>
                    <Text {...infoColor(key, label)}>{glue(value.slice(0, -INACTIVE_SUFFIX.length))}</Text>
                    <Text dimColor>{glue(INACTIVE_SUFFIX)}</Text>
                  </>
                ) : (
                  <Text color={level === 'on' ? ON : level && level !== 'calm' ? LEVEL[level] : VALUE} {...infoColor(key, label)}>
                    {glue(value)}
                  </Text>
                )}
              </Text>
            ))}
          </Text>
        ))}
        {/* As on desktop: explanations live in the pane; the line adds only advice, when there is one. */}
        {card.advice ? (
          <Text key="advice" color={DIM} wrap="wrap">
            {card.advice}
          </Text>
        ) : null}
      </Box>
    )
  }
  return (
    <Box flexDirection="column" paddingLeft={1}>
      {/* A rule parts the row from the engine's mode line above; a Box has no top-only border. */}
      <Box key="rule" height={1} marginTop={1} overflow="hidden">
        <Text color={RULE}>{'─'.repeat(terminalCells(v, buttons))}</Text>
      </Box>
      <Box flexDirection="row" flexWrap="wrap" columnGap={1}>
        {[
          ...parts,
          // The terminal counts ⓘ as one cell but Cascadia Mono draws it wider; the trailing space
          // widens the button so its hover highlight covers the whole glyph.
          <Box key="info" flexDirection="column" flexShrink={0}>
            <Box flexDirection="row" columnGap={1}>
              {name('info', 'ⓘ ')}
              <Text key="pane-sep" color={RULE}>·</Text>
              {Button ? <Button key="pane" label={PANE_BUTTON} dimColor onPress={() => togglePane()} /> : <Text dimColor>{PANE_BUTTON}</Text>}
              {buttons.flatMap(b => [
                <Text key={`sep-${b.key}`} color={RULE}>|</Text>,
                Button ? <Button key={`button-${b.key}`} label={b.label} dimColor onPress={() => {}} /> : <Text key={`button-${b.key}`} dimColor>{b.label}</Text>,
              ])}
            </Box>
            {pointer('info', 0)}
          </Box>,
        ].flatMap((part, i) => [...(i > 0 ? [<Text key={`rule-${i}`} color={RULE}>│</Text>] : []), part])}
      </Box>
      {open !== null ? detail(open) : null}
    </Box>
  )
}
