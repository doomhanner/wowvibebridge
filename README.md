# WoWVibeBridge

A small kit for vibe-coding addons for **World of Warcraft: Forever** with an AI agent such as
Claude Code. The agent cannot see the game, so everything comes back through files on disk: the
**DevBridge** helper addon records Lua errors, debug lines and API probe results into its saved
data, and the **wowdev** tool deploys your addons, checks them against the Forever API and reads
that saved data back as JSON.

```
addons/<Name>/  --lint-->  --deploy (copy)-->  _forever_\Interface\AddOns\<Name>\
                                                        |
                                           restart or /reload in game
                                                        |
WTF\...\SavedVariables\DevBridge.lua  <--written on /reload, logout, exit--
        |
   wowdev sv DevBridge  -->  errors, log lines, probe results as JSON
```

## Before you rely on it

- **One client.** Built for WoW: Forever 1.60.1 (interface 16001, build 70888 at the time of
  writing). The API facts in the docs carry the build they were seen on; each patch can change
  them.
- **Windows only, so far.** Developed and tested on Windows 10. The Python tools are plain
  standard library and may work elsewhere, but nobody has tried.
- **Small and young.** One player built it alongside their first addon. Expect rough edges.
- Not affiliated with or endorsed by Blizzard Entertainment.

## What is in it

| Path | What |
|---|---|
| `devaddons/DevBridge/` | Helper addon: captures Lua errors with stacks, a debug log, API probes and a full API inventory into `DevBridgeDB`. In game: `/dvb` |
| `addons/` | Your addons. Empty to start; `wowdev new` creates them here |
| `templates/addon/` | What `wowdev new` copies: a settings page in Options > AddOns, every event handler wrapped so one error cannot break the addon, and a worked example of handling hidden ("secret") game values |
| `tools/wowdev.py` | `new`, `lint`, `deploy`, `status`, `sv`, `undeploy`, `export-api` |
| `tools/apicheck.py` | Checks that every global function, `C_` namespace call and event name your addons use really exists on Forever |
| `tools/luasv.py` | Reads the game's saved-data files (Lua tables) |
| `docs/wiki/research/forever-addon-api.md` | Fact sheet: what is known about the Forever addon API, each claim tagged VERIFIED, CONFIRMED, REPORTED or PROBE PENDING |
| `docs/wiki/guides/dev-loop.md` | Every command and when the game needs a restart instead of a `/reload` |
| `docs/wiki/research/2026-11-10-tooling-decisions.md` | Why each checker was chosen, and proof that each one can fail |
| `CLAUDE.md` | Rules for the AI agent. Claude Code reads it automatically; point other agents at it |

## Setup

You need Python 3.8 or newer, git, and the Forever client installed. The `gh` commands below
need the [GitHub CLI](https://cli.github.com/); you can also download the same files by hand from
each project's Releases page.

1. Clone this repository.
2. Edit `wowdev.json`: set `game_dir` to your `_forever_` folder (the default is the standard
   install path) and `author` to the name your addons should show.
3. Download the checkers and reference lists. They are not included, so they stay at the
   versions their owners publish:

   ```bash
   mkdir -p tools/vendor tools/bin
   cd tools/vendor
   git clone --depth 1 --single-branch --branch forever https://github.com/Gethe/wow-ui-source.git wow-ui-source
   git clone --depth 1 --single-branch --branch forever https://github.com/Ketho/BlizzardInterfaceResources.git BlizzardInterfaceResources
   cd ../bin
   gh release download v1.2.0 -R lunarmodules/luacheck -p luacheck.exe
   gh release download v0.36.0 -R TradeSkillMaster/wowlua-ls -p wowlua_ls-x86_64-pc-windows-msvc.exe -p checksums.txt
   mv wowlua_ls-x86_64-pc-windows-msvc.exe wowlua_ls.exe
   cd ../..
   ```

   Check `wowlua_ls.exe` against the SHA-256 in `checksums.txt` before running it.
4. `python tools/wowdev.py lint` should exit 0. Exit 3 means a checker or list is missing, and
   is never a pass.
5. `python tools/wowdev.py deploy DevBridge`, then start (or restart) the game. At login DevBridge
   prints one line with its session number and probe count.
6. Optional but worth it: in game type `/dvb api`, then `/reload`, then run
   `python tools/wowdev.py export-api`. apicheck then checks against the exact API of your build
   instead of the community list.

Run the tool tests with `python -m unittest discover -s tools -p "test_*.py"`.

## The loop

1. `python tools/wowdev.py new MyAddon --title "My Addon"` creates `addons/MyAddon/`.
2. Edit, then `python tools/wowdev.py lint` until it exits 0.
3. `python tools/wowdev.py deploy MyAddon`. It says whether the game needs a restart (a new addon
   folder or new image/sound files) or just `/reload`.
4. Play. Type `/dvb mark some note` before trying something, so the log lines up with what you
   did.
5. `/reload`, then `python tools/wowdev.py sv DevBridge` to read errors, log lines and probe
   results. Saved data is only written on `/reload`, logout or exit, so the command prints the
   file's age first.

Full command list: [the dev loop](docs/wiki/guides/dev-loop.md).

## Safety

- `deploy` copies files; it never links your source into the game folder. It only ever writes to
  or removes addon folders it created itself (each carries a `.wowdev-managed` marker), so other
  addons you have installed are left alone. It refuses to work through symlinks or junctions.
- Addons cannot cast spells or act in combat on their own; the template and the fact sheet show
  how to work within Forever's rules, including the "secret values" that hide combat data from
  addons.
- Your saved-data files contain your account and character folder names. Keep them out of
  anything you publish.

## Credits

The downloads come from [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source),
[Ketho/BlizzardInterfaceResources](https://github.com/Ketho/BlizzardInterfaceResources),
[luacheck](https://github.com/lunarmodules/luacheck) and
[wowlua-ls](https://github.com/TradeSkillMaster/wowlua-ls). Ideas borrowed from
[Atraeau/WoW-Addons](https://github.com/Atraeau/WoW-Addons) (in-game API export) and
[ForeverPlusPlus](https://github.com/xIGBClutchIx/ForeverPlusPlus) (in-game self-tests).

## License

MIT; see [LICENSE](LICENSE).
