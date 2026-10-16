import { advisorView, alerts, BAR, CALM_FILL, cardFor, COLD, desktopModelColor, DIM, dim, displayModel, EFFORT_COLORS, type Gauge, GROUPS, IDLE_MARK, isKnownFamily, LABEL, label, LEVEL, limitStyle, listOrder, modelColor, ON, type Part, readings, type Row, RULE, t, TRACK, VALUE } from './cards'
import { barSvg, cacheIconSvg, type Card, type CardBar, DESKTOP_COLD, DESKTOP_LEVEL, DESKTOP_ON, pieSvg, ringMarksSvg, ruleSvg, type Tone, wideBarSvg } from './desktop'
import type { View } from './view'

// The element tables differ per surface; these trees use only what all of them share,
// plus Svg where `isRemote` says the surface has it.
type Ctor = (props: any) => any
export type Els = { Box: Ctor; Text: Ctor; Svg?: Ctor; Button?: Ctor }

function drawRow(els: Els, row: Row, isRemote: boolean, key: string) {
  const { Box, Text, Svg } = els
  if (!isRemote || !Svg) {
    return (
      <Text key={key} wrap="truncate-end">
        {row.map((p, i) =>
          'wide' in p ? (
            <Text key={String(i)}>{wideCells(Text, p.wide, p.cells)}</Text>
          ) : (
            <Text key={String(i)} color={p.color} bold={p.bold}>
              {p.text}
            </Text>
          ),
        )}
      </Text>
    )
  }
  // Remote surfaces lay a row out in flex boxes, where each text run would wrap on its own.
  // A row is split at its `│` rules into groups that never break inside; only the gaps wrap.
  const groups: Part[][] = [[]]
  for (const p of row) {
    if (p === BAR) groups.push([])
    else groups[groups.length - 1]?.push(p)
  }
  const group = (parts: Part[], gk: string) => {
    const items: any[] = []
    let run: Part[] = []
    const flush = () => {
      if (!run.length) return
      items.push(
        <Text key={`t${items.length}`} wrap="truncate-end">
          {run.map((p, i) =>
            'text' in p ? (
              <Text key={String(i)} color={p.color} bold={p.bold}>
                {p.text}
              </Text>
            ) : null,
          )}
        </Text>,
      )
      run = []
    }
    for (const p of parts) {
      if ('wide' in p) {
        flush()
        const bar = wideBarSvg(p.wide, p.cells * 8)
        items.push(<Svg key={`w${items.length}`} source={bar.source} alt={bar.alt} width={bar.width} height={bar.height} />)
      } else run.push(p)
    }
    flush()
    return (
      <Box key={gk} flexDirection="row" alignItems="center" flexShrink={0} columnGap={1}>
        {items}
      </Box>
    )
  }
  return (
    <Box key={key} flexDirection="row" flexWrap="wrap" alignItems="center" columnGap={3}>
      {groups.filter(g => g.length).map((g, i) => group(g, `${key}-${i}`))}
    </Box>
  )
}

// The wide bar in cells: used in the level's colour, the projection dim, the rest on the gauges'
// track, the even-pace tick as ┃. A calm share takes the value colour, since the calm grey is the
// projection's.
function wideCells(Text: Ctor, b: CardBar, cells: number) {
  const at = (pct: number) => Math.round((Math.max(0, Math.min(100, pct)) / 100) * cells)
  const used = at(b.used)
  const projected = Math.max(used, b.projected !== undefined ? at(b.projected) : used)
  const tick = b.tick === null ? -1 : Math.min(cells - 1, at(b.tick))
  const usedColor = b.level === 'calm' ? VALUE : LEVEL[b.level]
  return Array.from({ length: cells }, (_, i) => (
    <Text key={String(i)} color={i === tick ? VALUE : i < used ? usedColor : i < projected ? DIM : TRACK}>
      {i === tick ? '┃' : i < projected ? '━' : '─'}
    </Text>
  ))
}

// The pane: every card of the row, unfolded in the row's order, with all of its explanation; the
// mobile app's only view. What needs a decision leads, and only while something does.

// A pane bar spans the readings column, labels and values, within these bounds.
const PANE_BAR_MIN = 20
const PANE_BAR_MAX = 40
// Cells between a reading's label and its value.
const PANE_LABEL_GAP = 2


const toneColor = (tone?: Tone) => (tone === 'on' ? ON : tone && tone !== 'calm' ? LEVEL[tone] : VALUE)

export function drawPane(els: Els, v: View, isRemote: boolean) {
  const { Box, Text } = els
  const { gauges, info, advisor } = readings(v)
  const m = v.meta
  // A section's lines: a Row drawn as text runs, or a node laid out on its own (the readings grid).
  const sections: { key: string; rows: (Row | { node: any })[] }[] = []
  const urgent = alerts(v)
  const extras = (card: Card) => [...card.bullets, ...(isRemote ? (card.desktopBullets ?? []) : [])].filter(b => b !== 'Full detail: /pitboard')
  // One reading to a line, every card's labels in one column as wide as the longest, values left
  // after it: the pane reads top to bottom as one list, aligned in any font and at any width.
  const cards = [...gauges.map(g => g.card), ...(advisor ? [advisor] : []), info]
  const labelWidth = Math.max(0, ...cards.flatMap(c => c.rows.map(([l]) => l.length))) + PANE_LABEL_GAP
  const valueWidth = Math.max(0, ...cards.flatMap(c => c.rows.map(([, val]) => val.length)))
  const barCells = Math.min(PANE_BAR_MAX, Math.max(PANE_BAR_MIN, labelWidth + valueWidth))
  const grid = (key: string, card: Card) =>
    listOrder(card.rows)
      .map(([l, val, tone], i) => ({
        node: (
          <Box key={`${key}-r${i}`} flexDirection="row" paddingLeft={2}>
            <Box width={labelWidth} flexShrink={0}>
              <Text color={LABEL}>{l}</Text>
            </Box>
            <Text color={toneColor(tone)}>{val}</Text>
          </Box>
        ),
      }))
  // Bullets wrap under their own indent; the pane is read, not glanced at.
  const bullet = (key: string, text: string) => ({
    node: (
      <Box key={key} flexDirection="row" alignItems="flex-start" paddingLeft={2}>
        <Text color={DIM}>{'• '}</Text>
        <Text color={DIM} wrap="wrap">
          {text}
        </Text>
      </Box>
    ),
  })
  // What needs a decision leads: each warning's head on its own line, its advice wrapped under it.
  if (urgent.length)
    sections.push({
      key: 'attention',
      rows: [
        [t('Needs attention', LEVEL[urgent[0]?.level ?? 'watch'], true)],
        ...urgent.flatMap((a, i): (Row | { node: any })[] => [
          [t(a.head, LEVEL[a.level], true)],
          {
            node: (
              <Box key={`attention-${i}`} paddingLeft={2}>
                <Text color={VALUE} wrap="wrap">
                  {a.advice}
                </Text>
              </Box>
            ),
          },
        ]),
      ],
    })
  const unfold = (key: string, card: Card, head: Row) => {
    const rows: (Row | { node: any })[] = [[t(card.title, card.level && card.level !== 'calm' ? LEVEL[card.level] : VALUE, true), ...head], ...grid(key, card)]
    if (card.bar) rows.push([dim('  '), { wide: card.bar, cells: barCells }])
    extras(card).forEach((b, i) => rows.push(bullet(`${key}-b${i}`, b)))
    sections.push({ key, rows })
  }
  for (const g of gauges) {
    const head: Row = g.icon ? [t(` ${g.icon === 'hot' ? '🔥' : '❄'}`, g.icon === 'cold' ? COLD : LEVEL.act)] : g.mark ? [t(` ${markText(g)}`, ON)] : []
    unfold(g.key, g.card, head)
    if (g.key === 'context' && advisor && m?.advisor) unfold('advisor', advisor, [])
  }
  // ⓘ's card is titled by the model; the pane names the section and keeps the model beside it.
  if (m) unfold('info', { ...info, title: 'Session' }, [t(` ${info.title}`, modelColor(info.title))])
  return (
    <Box flexDirection="column">
      {sections.map(s => (
        <Box key={s.key} flexDirection="column" marginBottom={1}>
          {s.rows.map((row, i) => ('node' in row ? row.node : drawRow(els, row, isRemote, `${s.key}-${i}`)))}
        </Box>
      ))}
    </Box>
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
const NATIVE_CELLS_PER_CHAR = 1.15
// A faint grey wash under the hovered reading: a border takes a whole cell on each side and wraps
// the row, a background takes no room. Half-transparent grey shades a light band and lifts a dark one.
const HOVER_FRAME = { backgroundColor: '#8080801f' }


export function drawDesktop(els: Els, v: View, open: string | null, toggle: (key: string) => void, columns = Infinity, isPaneOpen = false) {
  const { Box, Button, Svg, Text } = els
  const { gauges, info, advisor, advisorOn } = readings(v)
  const style = limitStyle(v, columns)
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
      const bar = barSvg(g.pct ?? 0, g.level, g.pace)
      return [
        name(g.key, g.label),
        Svg ? <Svg key={`bar-${g.key}`} source={bar.source} alt={`${g.label} ${bar.alt}`} width={bar.width} height={bar.height} /> : null,
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
          : pieSvg(g.pct, g.level)
    return [
      name(g.key, g.label),
      ring && Svg ? <Svg key={`pie-${g.key}`} source={ring.source} alt={`${g.label} ${g.icon || (g.mark && Svg) ? ring.alt : (g.value ?? 'n/a')}`} width={ring.width} height={ring.height} /> : null,
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
        {Svg ? <Svg key="rule-info" source={rule.source} alt={rule.alt} width={rule.width} height={rule.height} /> : null}
        <Box key="info" position="relative" flexShrink={0} paddingX={1} hover={HOVER_FRAME}>
          {name('info', 'ⓘ')}
          {HOVER_CARDS || open === 'info' ? popover(info) : null}
        </Box>
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

// The pane button beside ⓘ, drawn as plainly as the row's other names.
const PANE_BUTTON = 'pitboard'

// The state glyphs drawn between a reading's gauge and its value: ↻, and ᶻ (one cell) while idle compaction is expected.
const markText = (g: Gauge) => `${g.mark ?? ''}${g.idle ? IDLE_MARK : ''}`

// The terminal row's width in cells, from the same pieces drawTerminal draws, so the rule above spans
// the row alone. Gaps: 1 inside a reading, 1 + · + 1 between readings, 1 + │ + 1 between groups; the
// last part is ⓘ (2), 1 + · + 1 and the pane button, and the rule runs 1 past it so the button sits as far
// from its end as ⓘ from the │ before it.
export function terminalCells(v: View): number {
  const { gauges, advisorOn } = readings(v)
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
  return [...groupWidths, 2 + 3 + PANE_BUTTON.length].reduce((sum, w, i) => sum + w + (i > 0 ? 3 : 0), 0) + 1
}

export function drawTerminal(els: Els, v: View, open: string | null, toggle: (key: string) => void, openPane: () => void = () => {}) {
  const { Box, Button, Text } = els
  const { gauges, info, advisor, advisorOn } = readings(v)
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
        <Text color={g.level === 'calm' ? CALM_FILL : LEVEL[g.level]} backgroundColor={TRACK}>
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
  const detail = (key: string) => {
    // The advisor's card exists whenever meta does; with no advisor set its line has no label to hang from.
    const card = key === 'advisor' ? (m?.advisor ? advisor : undefined) : cardFor(gauges, info, key)
    if (!card) return null
    const titleColor = isInfoOf(key) && model ? modelColor(model) : undefined
    return (
      <Box key={`detail-${key}`} flexDirection="column">
        {detailLines(card).map((line, n) => (
          <Text key={`line-${n}`} wrap="truncate-end">
            {n === 0 ? (
              <Text bold color={titleColor ?? (card.level && card.level !== 'calm' ? LEVEL[card.level] : VALUE)}>
                {card.title}
              </Text>
            ) : null}
            {line.rows.map(([label, value, level], i) => (
              <Text key={String(i)}>
                <Text color={DIM}>{`${n === 0 || i > 0 ? ' · ' : ''}${label} `}</Text>
                {isInfoOf(key) && label === 'Advisor' && isInactive && value.endsWith(INACTIVE_SUFFIX) ? (
                  <>
                    <Text {...infoColor(key, label)}>{value.slice(0, -INACTIVE_SUFFIX.length)}</Text>
                    <Text dimColor>{INACTIVE_SUFFIX}</Text>
                  </>
                ) : (
                  <Text color={level === 'on' ? ON : level && level !== 'calm' ? LEVEL[level] : VALUE} {...infoColor(key, label)}>
                    {value}
                  </Text>
                )}
              </Text>
            ))}
          </Text>
        ))}
        {/* As on desktop: explanations live in the pane; the line adds only advice, when there is one. */}
        {card.advice ? (
          <Text key="advice" color={DIM} wrap="truncate-end">
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
        <Text color={RULE}>{'─'.repeat(terminalCells(v))}</Text>
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
              {Button ? <Button key="pane" label={PANE_BUTTON} plain dimColor onPress={() => openPane()} /> : <Text dimColor>{PANE_BUTTON}</Text>}
            </Box>
            {pointer('info', 0)}
          </Box>,
        ].flatMap((part, i) => [...(i > 0 ? [<Text key={`rule-${i}`} color={RULE}>│</Text>] : []), part])}
      </Box>
      {open !== null ? detail(open) : null}
    </Box>
  )
}
