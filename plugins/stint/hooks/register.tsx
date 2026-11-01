import type { EngineInterface, Register } from 'claude-code'

import { isDue, limitsKey, limitsPath, limitsReading, stateDirOf } from './limits'
import type { RateLimit, Sent } from './limits'

// Every function that takes $ lives in this file: the engine follows $ only within the file that declares it.

// Stint's state directory by the rule scripts/state.py uses, so the module and the scripts agree.
async function stateDir($: EngineInterface): Promise<string> {
  return stateDirOf({
    stateDir: await $.env.get('STINT_STATE_DIR'),
    configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
    home: (await $.env.get('USERPROFILE')) || (await $.env.get('HOME')),
  })
}

// The last write of this process: the figures it held and when.
let sent: Sent | null = null

async function writeLimits($: EngineInterface, limits: readonly RateLimit[]) {
  const key = limitsKey(limits)
  if (key === null) return
  const now = await $.clock.now()
  if (!isDue(key, sent, now)) return
  try {
    const [dir, id] = await Promise.all([stateDir($), $.session.id()])
    await $.fs.write(limitsPath(dir), JSON.stringify(limitsReading(limits, now, id)))
    sent = { key, at: now }
  } catch {
    // Nothing depends on one reading; the next one tries again.
  }
}

export const register: Register = on => {
  // Writes whether or not PaceLine is installed; never changes the step.
  on('session.measure', async ($, e, next) => {
    try {
      await writeLimits($, e.rateLimits ?? [])
    } catch {
      // A failed write never fails the step.
    }
    return next(e)
  })
}
