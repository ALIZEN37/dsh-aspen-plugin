// dsh-aspen-plugin — DSH cordis plugin (official registration path)
// ================================================================
// This plugin contributes ONE MCP server for its own lifetime through the
// host service `ctx.mcpManager` (the settings-driven MCP fleet). No row in the
// bundle patch names @deepseek-ai/dsh-mcp-client any more: that legacy form
// resolves from the profile's node_modules and silently fails when the profile
// holds an older copy whose peer packages are not installed.
//
// Exposed tools (server name "aspen"): mcp__aspen__aspen_status,
// aspen_open_case, aspen_run, aspen_get, aspen_set, aspen_list, aspen_save,
// aspen_close.
//
// Everything path-related is derived from this module's own location, so the
// package installs unchanged into any profile.

import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { existsSync } from 'node:fs'

export const name = 'dsh-aspen-plugin'

/** The manager owns the client instances; we only contribute an entry. */
export const inject = ['mcpManager']

/** Tool namespace: mcp__aspen__<tool>. Must be unique among configured+registered servers. */
const SERVER_NAME = 'aspen'

/** Bundled Python (pywin32+comtypes are vendored under deps/), overridable. */
function interpreterPath() {
  if (process.env.DSH_ASPEN_PYTHON) return process.env.DSH_ASPEN_PYTHON
  const home = process.env.USERPROFILE || process.env.HOME || ''
  const runtime = join('.dsh', 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', 'python.exe')
  const bundled = home ? join(home, runtime) : ''
  return bundled && existsSync(bundled) ? bundled : 'python'
}

export function apply(ctx) {
  const root = dirname(fileURLToPath(import.meta.url))
  const server = join(root, 'aspen_mcp_server.py')
  const manager = ctx.mcpManager
  if (!manager || typeof manager.register !== 'function') {
    console.error('[dsh-aspen-plugin] ctx.mcpManager is unavailable; no Aspen tools registered')
    return
  }
  if (!existsSync(server)) {
    console.error(`[dsh-aspen-plugin] MCP server not found at ${server}; no Aspen tools registered`)
    return
  }

  try {
    const dispose = manager.register(SERVER_NAME, {
      transport: 'stdio',
      command: interpreterPath(),
      args: ['-u', '-X', 'utf8', server],
      cwd: root,
      env: { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
      // A cold COM start of Aspen Plus takes tens of seconds; a simulation run
      // can take minutes, so both budgets are deliberately generous.
      startupTimeoutMs: 120_000,
      toolCallTimeoutMs: 600_000,
    })
    if (typeof dispose === 'function' && typeof ctx.on === 'function') {
      ctx.on('dispose', dispose)
    }
  } catch (error) {
    // Never let a rejected registration break host boot.
    console.error('[dsh-aspen-plugin] mcpManager.register(aspen) failed:', error)
  }
}
