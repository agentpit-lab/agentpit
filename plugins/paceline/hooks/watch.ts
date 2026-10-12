// Pure part of the inbox watch: no engine calls, so tests reach it directly.

export type Listed = { name: string; kind: string; size: number; mtimeMs: number }

// A new reading reaches the limits file within one period; pitboard re-reads it every 20 s.
export const WATCH_PERIOD_MS = 15_000

// The order the command hooks try; a process run has no shell to test a command with.
export const PYTHONS = ['python3', 'python'] as const

// What a merge is compared by: each reading file's name, size and modification time.
export function inboxFingerprint(entries: readonly Listed[]): string {
  return entries
    .filter(e => e.kind === 'file' && e.name.endsWith('.json'))
    .map(e => `${e.name}:${e.size}:${e.mtimeMs}`)
    .sort()
    .join('\n')
}

// The fingerprint last handed to a merge, shared by every session on the machine.
export const seenPath = (stateDir: string) => `${stateDir}/inbox.seen`
