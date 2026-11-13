# WoWVibeBridge: instructions for the AI agent

Addon development for the WoW **Forever** client (1.60.1, interface **16001**, a Retail 12.x-based
client with its own API additions and removals). The player plays and directs; the agent writes
the addons and cannot see the game, so every result comes back through files on disk. Read
[the dev loop](docs/wiki/guides/dev-loop.md) and [the fact sheet](docs/wiki/research/forever-addon-api.md)
before writing addon code.

## Layout

| Path | What |
|---|---|
| `addons/<Name>/` | The player's addons. Source of truth; deployed by copying, never edited in the game folder |
| `devaddons/DevBridge/` | Helper addon: captures errors, debug log, API probes and API inventory into `DevBridgeDB` |
| `templates/addon/` | Scaffold used by `wowdev new` |
| `tools/wowdev.py` | new / lint / deploy / undeploy / status / sv / export-api |
| `tools/apicheck.py` | Gate: every global call, `C_` call and event name must exist on Forever |
| `tools/luasv.py` | SavedVariables (Lua table) parser |
| `tools/bin/`, `tools/vendor/` | Gitignored downloads: checkers and reference clones (README.md, Setup) |
| `data/` | Gitignored exports from the game (`api-inventory.json`) |
| `wowdev.json` | Game folder, interface number, source folders, author name |

## Invariants

- **Lint before deploy.** `python tools/wowdev.py lint` exits 0 before any deploy. Exit 3 means a checker is missing, which is not a pass.
- **Check the API against Forever, not Retail or Classic.** Look things up in `tools/vendor/wow-ui-source` (the `forever` branch) and the fact sheet. Never trust wowlua-ls alone for "does this exist": its stubs accept removed globals such as `GetSpellInfo`.
- **Settle disputes with a probe, not a guess.** Unknown client behaviour gets a DevBridge probe (`devaddons/DevBridge/Probes.lua`); the result goes into the fact sheet with its status tag, build number and date.
- **Never touch addons wowdev did not deploy.** Deploy refuses any target folder without the `.wowdev-managed` marker. Never deploy through junctions or symlinks.
- **Secret values.** Combat-related data (auras, cooldowns, combat log, much unit data) is opaque to addons in combat and instances. Design around it; guard unknown values before comparing or doing arithmetic.
- **Guard possibly-secret values with `canaccessvalue(x)` directly** (not a wrapper), so wowlua-ls can see the guard. The guard goes **first**: a true/false test (`if x`, `not x`) on a hidden value errors, and a readable table can hold hidden fields (`UNIT_AURA`'s `isFullUpdate` while grouped), so check each field before testing it and tables with `canaccesstable`. No linter catches the wrong order ([fact sheet](docs/wiki/research/forever-addon-api.md#secret-values-addon-restrictions)).
- **Registering an unknown event errors and stops the file.** apicheck checks literal event names; anything dynamic goes through `pcall`.
- **Addons cannot cast spells by themselves.** Casting needs a real key press or click; arm a `SecureActionButtonTemplate`, or rewrite a macro the player keeps on an action bar (`EditMacro`, out of combat), and let the player press it. Secure attributes, override bindings and showing/hiding frames that hold secure buttons are blocked in combat: defer to `PLAYER_REGEN_ENABLED`.
- **SavedVariables are only written on `/reload`, logout or exit.** `wowdev sv` prints the file's age; read the age before trusting the data.
- **Helper addons stay in `devaddons/`, the player's addons in `addons/`.** An addon name in both is refused.
- **In a git worktree, deploy by name.** `wowdev deploy` with no names is refused there; lint borrows the main checkout's checkers.
- **Source files are ASCII-only** (Lua, TOC, Python). Python tooling is stdlib-only.
- Each addon declares its owned globals in `.luacheckrc` (`wowdev new` does this); anything else written to `_G` is a bug.
- **Privacy.** `wowdev status`, `wowdev sv` and the game's `WTF` folder show the player's account and character folder names, and DevBridge logs can hold other players' names. Never copy them into docs, commit messages or anything shared; write `<ACCOUNT>` or `<player>` instead.

## The player's loop

The agent deploys and says whether the game needs a **restart** (new addon folder or new media
files) or just **`/reload`** (everything else). The player does that, then optionally types
`/dvb mark <note>` before trying a feature. The agent reads results with
`python tools/wowdev.py sv DevBridge`.

## Keeping the fact sheet true

When a probe or a play test settles a question, update the claim's tag and source in the fact
sheet with the build number and date, and keep the old source. DevBridge's login line shows the
client build; after a patch, re-export the API list (in game `/dvb api`, `/reload`, then
`python tools/wowdev.py export-api`) and re-run lint.
