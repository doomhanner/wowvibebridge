# The dev loop

How an addon goes from source to the game and how results come back. An AI agent (Claude Code
here) runs the commands; the player restarts or reloads the game and plays.

Addons live in two source folders: `addons/` holds the addons you build, and `devaddons/` holds
the helper addons that come with the setup (DevBridge). Every command below looks in both; an
addon name that exists in both is refused. `new` always creates in `addons/`.

```
addons/<Name>/  --lint-->  --deploy (copy)-->  _forever_\Interface\AddOns\<Name>\
                                                        |
                                           restart or /reload in game
                                                        |
WTF\...\SavedVariables\DevBridge.lua  <--written on /reload, logout, exit--
        |
   wowdev sv DevBridge  -->  errors, log lines, probe results as JSON
```

## Commands (run from the project root)

| Step | Command | Notes |
|---|---|---|
| New addon | `python tools/wowdev.py new MyAddon --title "My Addon"` | Copies `templates/addon/` and registers its globals in `.luacheckrc`. The new addon has a settings page in Options > AddOns with an "Enabled" tickbox (`Options.lua`; `/myaddon options` opens it, `/myaddon toggle` flips it), every event handler wrapped so an error is recorded instead of breaking the addon, and a commented example of checking hidden ("secret") game values first. It lints clean as made |
| Check | `python tools/wowdev.py lint` | luacheck + wowlua-ls + apicheck. Exit 0 = clean, 1 = findings, 3 = a checker is missing (never a pass) |
| Deploy | `python tools/wowdev.py deploy [Name ...]` | Copies changed files; prints whether a restart or `/reload` is needed. In a git worktree the names are required |
| State | `python tools/wowdev.py status` | In sync / stale / not deployed / conflict, and whether Wow.exe is running |
| Read results | `python tools/wowdev.py sv DevBridge` | Prints the file's age first: data is only as fresh as the last `/reload` |
| Refresh API list | in game `/dvb api`, `/reload`, then `python tools/wowdev.py export-api` | Writes `data/api-inventory.json`; apicheck prefers it over Ketho's list |
| Remove | `python tools/wowdev.py undeploy Name` | Only removes folders deploy created (they carry a `.wowdev-managed` marker) |
| Tests | `python -m unittest discover -s tools -p "test_*.py"` | Tooling tests (100 on 2026-11-11) |

## Working from a git worktree (app sessions)

App sessions usually run in their own worktree (in a separate worktrees folder), a second copy of the
repository on its own `claude/*` branch. A worktree has none of the gitignored files: no
`tools/bin` (luacheck, wowlua-ls), no `tools/vendor` (API lists) and no `data/`.

- **Lint** finds them anyway: when the worktree has neither `tools/bin` nor `tools/vendor`, it reads them from the main checkout (read only) and prints `note: git worktree; checkers and API lists are read from the main checkout ...`. If the main checkout lacks them too, lint still exits 3. Never junction or symlink the folders in instead: `git worktree remove --force` follows links and deletes the target.
- **Deploy needs addon names** in a worktree (`deploy MyAddon`). Deploying everything from a branch that is behind would overwrite newer copies of other addons, DevBridge included, in the game folder.
- **Start from an up-to-date `main`.** A worktree only has what its branch has; merge finished work into `main` before starting new sessions.
- SavedVariables and the game folder are shared by every session; `wowdev sv` reads the same file from anywhere.

## When the game needs a restart vs a /reload

- **Restart**: the first deploy of a new addon folder, or new image/sound/font files.
- **/reload**: everything else (edited or new Lua/XML files, TOC edits). Type `/reload` in chat.
- Deploy prints which one applies. The rule comes from the fact sheet and is not yet confirmed on Forever; if a `/reload` ever fails to pick up a change, restart and record it in the log.

## DevBridge in game

DevBridge is our helper addon. It records Lua errors (with stack), blocked-action warnings, debug
lines from our addons and probe results into its SavedVariables, and prints one line at login:
`DevBridge: session N, client 1.60.1 (build 70862, interface 16001). X probes run, Y failed. Z error(s) this session.`

| Chat command | Effect |
|---|---|
| `/dvb` or `/dvb status` | Counts and the command list |
| `/dvb errors` | Last five recorded errors in chat |
| `/dvb mark some text` | Puts a marker in the log ("just opened the bank"), to line up logs with what you did |
| `/dvb probe` | Re-runs the probes (for example after entering combat or an instance). Kept as `manualProbes` until the next `/dvb probe`; the automatic run at every login (including `/reload`) only replaces `probes` |
| `/dvb api` | Takes a full inventory of global functions and `C_` namespaces |
| `/dvb clear` | Empties the error and log lists |

After any command, `/reload` writes the results to disk.

Our addons write debug lines with `if DevBridge then DevBridge.Log("MyAddon", ...) end`
(the template's `log()` helper does this) and declare `## OptionalDeps: DevBridge` so DevBridge
loads first and catches their errors.

## Writing a probe

To settle an API question in the real client, add a probe to `devaddons/DevBridge/Probes.lua`:

```lua
probe("spell.fireballInfo", function()
  return DevBridge.Call("C_Spell.GetSpellInfo", 133)  -- never raises; reports missing/error/ok
end)
```

Deploy, `/reload`, then `python tools/wowdev.py sv DevBridge --var DevBridgeDB` and read
`probes["spell.fireballInfo"]`. Record what it showed in the fact sheet.
