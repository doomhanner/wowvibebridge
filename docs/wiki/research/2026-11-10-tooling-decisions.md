# 2026-11-10 - Dev tooling survey and decisions

What we installed for Forever addon work, why, and how each checker was shown to be able to
fail. Tool binaries and reference clones live in gitignored `tools/bin/` and `tools/vendor/`;
re-download them on a fresh checkout (commands at the bottom).

## Decisions

| Need | Choice | Why | Rejected |
|---|---|---|---|
| Ground-truth Blizzard UI code and API docs | Gethe/wow-ui-source `forever` branch, shallow clone (52 MB, build 1.60.1.70862, same as the client) | Exact build match, no in-game console steps | `ExportInterfaceFiles` (needs `-console` and manual steps; untested on Forever) |
| Lists of every global, event, CVar | Ketho/BlizzardInterfaceResources `forever` branch (31 MB, build 70532) | Feeds apicheck's fallback global list; 7,087 API globals plus FrameXML names | |
| Lua hygiene lint | luacheck 1.2.0 (`tools/bin/luacheck.exe`, 0.8 MB, no published checksum, size matches release) | Mature; catches accidental globals, unused variables, shadowing | selene (no WoW std), StyLua (formatter only, not needed yet) |
| WoW type checking | wowlua-ls 0.36.0 (`tools/bin/wowlua_ls.exe`, 40 MB, SHA-256 matched release `checksums.txt`) | Knows the WoW API with types; catches typos and wrong argument types; has a CLI that exits non-zero | Ketho's vscode-wow-api / LuaLS annotations: stop at Retail 12.0.x, no Forever data |
| "Does this API exist on Forever?" | `tools/apicheck.py` (written here) | wowlua-ls fails this job (see below) | |
| Deploy into the game | Copy-mirror with an ownership marker (`tools/wowdev.py deploy`) | Junctions risk deleting source through the link and let half-edited files load; copy is one extra command that Claude runs anyway | Junction/symlink deploy (used by Atraeau/WoW-Addons) |
| Error capture in game | DevBridge (our addon) | Writes errors to SavedVariables so Claude can read them from disk | BugSack/BugGrabber (have Forever builds; optional extra for an in-game error window) |

## Negative controls: proof each gate can fail

Ran `wowdev lint` against a throwaway addon of deliberate mistakes, then deleted it.

| Planted mistake | luacheck | wowlua-ls | apicheck |
|---|---|---|---|
| unused local | caught | - | - |
| accidental global inside a function | caught | caught | - |
| typo `GetBuildInf()` | - | caught | caught |
| wrong argument type `C_Timer.After("soon", fn)` | - | caught | - |
| removed global `GetSpellInfo(133)` | - | **missed** | caught |
| Classic-only `GetQuestLogTitle(1)` | - | **missed** | caught |
| typo inside a namespace `C_Spell.GetSpellInfoTypo` | - | not tested | caught |
| fake namespace `C_NotARealNamespace.Thing()` | - | not tested | caught |
| unknown event `RegisterEvent("NOT_A_REAL_EVENT")` | - | **missed** | caught |
| dead event `COMBAT_LOG_EVENT_UNFILTERED` | - | missed | caught (warning with replacement) |
| the same calls inside a comment or a string | - | - | correctly ignored |

**wowlua-ls gaps found:**
1. Its built-in stubs (`wowlua_ls dump-stubs`) still list `GetSpellInfo`, `GetItemInfo`, `UnitAura`, `GetQuestLogTitle` and `CombatLogGetCurrentEventInfo` as callable.
2. The `flavors` setting in `.wowluarc.json` had no visible effect from `wowlua_ls check`: with `"flavors": ["classic_era"]`, a Retail/Forever-only call (`C_WeeklyRewards.HasAvailableRewards`) was not flagged either. Possibly the TOC's `16001` intersects with the config; not investigated further.
Both are worth reporting upstream to TradeSkillMaster/wowlua-ls (see open follow-ups).

## apicheck in two sentences

apicheck reads every capitalised global call and `C_` namespace call in `addons/`, plus every
event name passed to `RegisterEvent`, and checks each against a list of what the Forever client
really has. The list comes from DevBridge's in-game inventory when one has been exported
(`data/api-inventory.json`), otherwise from Ketho's Forever lists; a missing list is a failure.

Known blind spots: lowercase globals (`strsplit`, `wipe`) are not checked; calls through
variables (`local f = GetSpellInfo; f()`) are not checked; event names built at runtime are
not checked. DevBridge registers its own events through `pcall` for that reason.

## Community setups we learned from

- [Atraeau/WoW-Addons](https://github.com/Atraeau/WoW-Addons): junction deploy with `-Watch`, an in-game API exporter addon (JSON + base64 into SavedVariables), generated LuaCATS stubs. DevBridge's `/dvb api` inventory is the same idea, smaller.
- [xIGBClutchIx/ForeverPlusPlus](https://github.com/xIGBClutchIx/ForeverPlusPlus): CI with `luac5.1 -p`, offline tests with stubbed globals, an ASCII-only check, a TOC-lists-every-file check, `/fpp selftest` in-game probes, and an experimental live game-to-PC bridge (see the idea inbox).

## Re-download commands

```bash
cd tools/vendor
git clone --depth 1 --single-branch --branch forever https://github.com/Gethe/wow-ui-source.git wow-ui-source
git clone --depth 1 --single-branch --branch forever https://github.com/Ketho/BlizzardInterfaceResources.git BlizzardInterfaceResources
cd ../bin
gh release download v1.2.0 -R lunarmodules/luacheck -p luacheck.exe
gh release download v0.36.0 -R TradeSkillMaster/wowlua-ls -p wowlua_ls-x86_64-pc-windows-msvc.exe -p checksums.txt
mv wowlua_ls-x86_64-pc-windows-msvc.exe wowlua_ls.exe
```
