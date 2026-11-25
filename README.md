# dsh-aspen-plugin

Drive **Aspen Plus V14** from DeepSeek Harness. The plugin contributes one MCP
server through the host's MCP manager and exposes eight tools:

| Tool | Purpose |
| --- | --- |
| `aspen_status` | Locate `aspenplus.exe`, report pywin32/COM availability and whether a document is open |
| `aspen_open_case` | Open a `.bkp` case, optionally run it |
| `aspen_run` | Run the current simulation and report convergence |
| `aspen_get` | Read one or more flowsheet tree paths (`\Data\Streams\1\Output\RES_TEMP`) |
| `aspen_set` | Write a tree node (block inputs are writable; stream inputs are restricted on this build) |
| `aspen_list` | List child nodes of a tree path, optionally with values |
| `aspen_save` | Save the open document |
| `aspen_close` | Close the document |

## How it registers (official path)

`index.js` is a cordis plugin:

```js
export const name = 'dsh-aspen-plugin'
export const inject = ['mcpManager']

export function apply(ctx) {
  ctx.mcpManager.register('aspen', { transport: 'stdio', command: <python>, args: [...], cwd: <plugin dir> })
}
```

The bundle patch `cordis.patch.yml` only mounts the plugin:

```yaml
- insert:
    - id: dsh-aspen-plugin
      name: 'dsh-aspen-plugin'
```

Do **not** add a row naming `@deepseek-ai/dsh-mcp-client`: that legacy form resolves the
client from the profile's `node_modules`, where an older copy can lack its
`@deepseek-ai/dsh-*` peers, and the loader drops the row silently.

All paths (interpreter, server script, cwd) are derived from `import.meta.url`, so the
package is machine-independent. `DSH_ASPEN_PYTHON` overrides the interpreter.

## Install

Copy the package into `<DSH_HOME>/profiles/<profile>/node_modules/dsh-aspen-plugin`,
list it in that profile's `package.json` under `dependencies` and
`dsh.profile.bundles`, then restart DSH. Tools appear as `mcp__aspen__*`.

## Install from the tarball

```bash
tar -xzf dsh-aspen-plugin-0.1.0.tgz
cp -r package/* <DSH_HOME>/profiles/<profile>/node_modules/dsh-aspen-plugin/
```

Then add the package to that profile's `package.json` under `dependencies` and
`dsh.profile.bundles`, and restart DSH. (npm/pnpm can also consume the tarball with
`npm install <path-to-tgz>` when installing into a fresh profile.)

## Permissions and prerequisites

- Aspen Plus V14 installed with a working Sentinel RMS licence (the licence service
  must be running).
- A logged-in, unlocked desktop session: Aspen is a GUI application and its COM server
  needs a window station.
- The host process (not a sandboxed shell) must be able to start `aspenplus.exe`.
  Starting Aspen inside a workspace-write file sandbox fails with
  `CO_E_SERVER_EXEC_FAILURE (0x80080005)`.
- The Python dependencies (pywin32, comtypes) are vendored under `deps/` and loaded via
  `sys.path` + `os.add_dll_directory`; the bundled DSH Python works as-is.

## Known limitation

On this installation, stream input nodes reject writes with `AE_UNDERSPEC` through COM
(`VASetValue`), while block inputs write normally. Work around it by authoring the case
in Aspen or by writing block-level specifications.

## Licence

MIT.