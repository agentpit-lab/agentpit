export type StintState = Record<string, never>

// Stint keeps no state in the engine: the module only writes a file. The empty entry keeps the
// plugin's state key reserved.
declare module 'claude-code' {
  interface PluginState {
    stint: StintState
  }
}
