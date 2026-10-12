// Pure part of the account resolver, copied from paceline's hooks/account.ts: a module can import only
// its own files, and the key must equal the one paceline tags readings with. One test vector, in
// tests/account.test.ts here and in paceline's, holds the copies and scripts/limits_file.py together.

export const ACCOUNT_PREFIX = 'paceline-account-v1'

export type AccountReason = 'no-file' | 'unreadable' | 'no-account'
// A key is 16 lower-case hex digits; reason says why there is none.
export type Account = { key: string; reason: null } | { key: null; reason: AccountReason }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

// Claude Code's own state file: <config>/.claude.json when CLAUDE_CONFIG_DIR is set, else
// <home>/.claude.json, never <home>/.claude/.claude.json, which holds no account.
export function accountFileOf(env: { configDir?: string | null; home?: string | null }): string {
  return env.configDir ? `${env.configDir}/.claude.json` : `${env.home ?? ''}/.claude.json`
}

function uuidOrNull(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const lower = value.toLowerCase()
  return UUID.test(lower) ? lower : null
}

// The text of .claude.json (null when it could not be read) to the account's one-way key. Only
// oauthAccount.accountUuid and oauthAccount.organizationUuid are touched.
export function accountKeyOf(text: string | null): Account {
  if (typeof text !== 'string') return { key: null, reason: 'unreadable' }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { key: null, reason: 'unreadable' }
  }
  const raw = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>).oauthAccount : null
  const oauth = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {}
  const account = uuidOrNull(oauth.accountUuid)
  const organization = uuidOrNull(oauth.organizationUuid) ?? ''
  if (account === null) return { key: null, reason: 'no-account' }
  return { key: sha256Hex(`${ACCOUNT_PREFIX}:${account}:${organization}`).slice(0, 16), reason: null }
}

function utf8(text: string): number[] {
  const out: number[] = []
  for (const ch of text) {
    const c = ch.codePointAt(0)!
    if (c < 0x80) out.push(c)
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63))
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63))
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63))
  }
  return out
}

const K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]

const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n))

// SHA-256 (FIPS 180-4) of the text's UTF-8 bytes, as 64 lower-case hex digits. Plain arithmetic, so the
// module needs no engine or platform crypto API.
export function sha256Hex(text: string): string {
  const bytes = utf8(text)
  const bitLength = bytes.length * 8
  bytes.push(0x80)
  while (bytes.length % 64 !== 56) bytes.push(0)
  // The length as a 64-bit big-endian number; 2^53 bits is far beyond any input here.
  const high = Math.floor(bitLength / 0x100000000)
  for (const word of [high, bitLength >>> 0]) bytes.push((word >>> 24) & 255, (word >>> 16) & 255, (word >>> 8) & 255, word & 255)

  const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
  const w = new Array<number>(64)
  for (let block = 0; block < bytes.length; block += 64) {
    for (let i = 0; i < 16; i++) {
      const j = block + i * 4
      w[i] = ((bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3]) >>> 0
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i++) {
      const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0
      hh = g
      g = f
      f = e
      e = (d + t1) >>> 0
      d = c
      c = b
      b = a
      a = (t1 + t2) >>> 0
    }
    const next = [a, b, c, d, e, f, g, hh]
    for (let i = 0; i < 8; i++) h[i] = (h[i] + next[i]) >>> 0
  }
  return h.map(x => x.toString(16).padStart(8, '0')).join('')
}
