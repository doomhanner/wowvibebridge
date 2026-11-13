# WoW: Forever addon API - fact sheet

Living fact sheet for writing addons against the Forever client. Every claim carries a status tag
and a source, and disputed claims name the DevBridge probe that will settle them. When a probe
result arrives, update the claim's tag here and add a log.md entry; do not delete the old source.

Started 2026-11-10 from three web research passes, the client files on this machine, and the
Blizzard UI source for build 70862 (`tools/vendor/wow-ui-source`, Gethe's `forever` branch).
"RRB" in the evidence columns is Reciprocal Rando Buffs, the first addon built with this setup; its
field tests are where several of these facts were first seen in play.

**Status tags**
- **VERIFIED**: seen on this machine (client files, SavedVariables, Blizzard source at our build, or a DevBridge probe result).
- **CONFIRMED**: Blizzard or Warcraft Wiki says so.
- **REPORTED**: fan site or a single developer. Treat as a hypothesis.
- **PROBE PENDING**: a DevBridge probe is written and deployed; waiting on an in-game run. Probe names refer to `devaddons/DevBridge/Probes.lua`.

**First probe run** (folded in 2026-11-10): DevBridge session 1, build 70862, SavedVariables saved
08:53 local; all 12 probes `ok`, no captured errors. Probes run once at `PLAYER_LOGIN` (and on
`/dvb probe`), so every result below describes the moment of login: out of combat
(`InCombatLockdown` false), open world, the test character (a Rogue, level 13, Alliance).
Tags read "VERIFIED (build 70862, 2026-11-10, `probe`)".

**The client patched to build 70888** (seen 2026-11-11: DevBridge session 10 and the in-game API
inventory both report 70888; the version string is still 1.60.1). Facts tagged 70862 were checked
on the earlier build and have not been re-checked unless noted. The reference clones under
`tools/vendor/` are still the 70862 source, so apicheck's event list is from 70862; its global list
now merges the client's own 70888 inventory (`data/api-inventory.json`: 6231 globals, 271 `C_`
namespaces) with Ketho's 70532 list. All 12 probes still ran `ok` on 70888 at login.

*Reading the raw data:* in a DevBridge `Call` result, `"<unstorable: false>"` means the call
returned `false` and `"<unstorable: nil>"` means `nil`. That is a DevBridge encoding quirk (the
`ok and clean or ...` idiom in `Call` treats a falsy value as a failure), not a client behaviour.
Fixed in DevBridge on 2026-11-10 after this run: later runs store `false` as `false` and `nil`
as `"<nil>"`.

## Client identity

| Fact | Status | Source |
|---|---|---|
| Product code `wowf`, version 1.60.1.70862, installed in the `_forever_` folder of the World of Warcraft install | VERIFIED | `.build.info`, `_forever_\.flavor.info` |
| Runs on the Retail engine ("Retail Build"), internal project name "Camelot" | VERIFIED | crash reports in `_forever_\Errors\*.txt` |
| Interface number is **16001** | VERIFIED + CONFIRMED | RXPGuides SavedVariables record `gameVersion = 16001`; [TOC format](https://warcraft.wiki.gg/wiki/TOC_format) |
| Shares Mainline's UI architecture and "the vast majority of APIs available in 12.1.5" | CONFIRMED | Blizzard statement quoted on [1.60.1 API change notes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes/notes) |
| Level cap 60 indefinitely; rulesets Normal, PvP, RP, RP-PvP, Hardcore later | CONFIRMED | [World of Warcraft: Forever](https://warcraft.wiki.gg/wiki/World_of_Warcraft:_Forever) |
| `WOW_PROJECT_CAMELOT = 18` exists; also `WOW_PROJECT_MAINLINE = 1`, `WOW_PROJECT_CLASSIC = 2` (the only `WOW_PROJECT_*` globals) | VERIFIED (build 70862, 2026-11-10, `client.projectId`); earlier CONFIRMED | [WOW_PROJECT_ID](https://warcraft.wiki.gg/wiki/WOW_PROJECT_ID) |
| **`WOW_PROJECT_ID` on Forever is 18 (`WOW_PROJECT_CAMELOT`), not 1.** Earlier the sources disagreed: ForeverPlusPlus said 1 (Mainline); wowforeverguides said 18 on build 70338; wowlua-ls v0.35 assumes 18. ForeverPlusPlus's 1 is wrong at our build | VERIFIED (build 70862, 2026-11-10, `client.projectId`); was PROBE PENDING | probe result `WOW_PROJECT_ID = 18` |
| `GetBuildInfo()` returns `"1.60.1"`, `"70862"`, `"Nov  6 2026"`, `16001` | VERIFIED (build 70862, 2026-11-10, `client.build`) | probe result |
| Reliable "am I on Forever" test: interface number in 16000..16999 | REPORTED; interface 16001 VERIFIED (`client.build`). `WOW_PROJECT_ID == WOW_PROJECT_CAMELOT` also works at 70862 | [ForeverPlusPlus docs](https://github.com/xIGBClutchIx/ForeverPlusPlus/blob/main/docs/forever-api.md) |
| `GetExpansionLevel()`, `GetServerExpansionLevel()` and `LE_EXPANSION_LEVEL_CURRENT` are all 0; `GetMaxPlayerLevel()` and `GetMaxLevelForPlayerExpansion()` are 60; `IsPublicBuild()` true, `IsTestBuild()` false; locale `enUS`, region 1 (`US`) | VERIFIED (build 70862, 2026-11-10, `client.environment`) | probe result. Realm names look internal (`CAMWOW US2 Realm <n> PvP`) |
| `C_GameRules` has 30 functions at 70862, including `IsHardcoreActive`, `IsSelfFoundAllowed`, `GetForeverExperiencePreset`, `GetActiveGameMode`, `IsGameRuleActive`, `GetGameRuleAsFloat`, `GetGameRuleAsFrameStrata`, `IsPlunderstorm`, `IsWoWHack`. On a normal realm: hardcore false, self-found false, active game mode 1, experience preset 1 | VERIFIED (build 70862, 2026-11-10, `client.gameRules`) | probe result |

## TOC files

| Fact | Status | Source |
|---|---|---|
| `## Interface: 16001`; a comma list serves several clients (`120105, 16001, 50504, 11509`) | CONFIRMED | [TOC format](https://warcraft.wiki.gg/wiki/TOC_format) |
| `_Forever.toc` and `_Camelot.toc` are both accepted; `_Mainline` is the family suffix and ranks lower; unmatched falls back to the plain `.toc` | CONFIRMED | TOC format |
| Which wins when both `_Forever` and `_Camelot` exist was undocumented; the BigWigs packager only recognises `_Camelot`. **Answer: `_Camelot` outranks `_Forever`, which outranks plain.** With plain, `_Mainline`, `_Camelot` and `_Forever` TOCs present, `_Camelot` loaded; with only plain and `_Forever`, `_Forever` loaded. Both suffixes work. (`_Mainline` vs `_Forever` and `_Mainline` vs plain were not tested pairwise; `_Camelot` beat `_Mainline`.) | VERIFIED (build 70862, 2026-11-10, `toc.suffixWinner`); was PROBE PENDING | TocProbe (all four suffixes) loaded "Camelot" and showed the title "TocProbe (Camelot)"; TocProbeF (plain vs `_Forever`) loaded "Forever" (`addons.list` agrees). Both were throwaway probe addons; they and the `toc.suffixWinner` probe were removed on 2026-11-12 once answered |
| `[Game]` expands to `Camelot`, not Forever (may change) | CONFIRMED | TOC format |
| Game-type names: `forever` (alias `camelot`); `mainline` family includes Forever | CONFIRMED | TOC format, [1.60.1 API changes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes) |
| Each game version has its own `Interface\AddOns` folder, so an addon deployed only to `_forever_` needs no load-type guards | VERIFIED | folder layout on this machine |
| Most published Forever addons ship one plain `.toc` with `16001` | REPORTED | GitHub survey of about 20 repos |

**Project rule:** our addons use one plain `<Name>.toc` with `## Interface: 16001`; the suffix result above gives no reason to add suffixed TOC files.
The result is in (2026-11-10): a plain TOC loads when it is the only one, so the rule still works
as written. Use `_Camelot` (not `_Forever`) only if an addon ever ships several TOCs for several
clients. Whether to change the rule is the manager's call; it is unchanged here.

## Loading, reloading, saving

| Fact | Status | Source |
|---|---|---|
| A brand-new addon folder needs a full client restart | REPORTED | ForeverPlusPlus; consistent with retail behaviour |
| `/reload` picks up edited and new `.lua`/`.xml` files and TOC changes in an addon the client already knows (since 9.0.1) | CONFIRMED for retail, not yet seen on Forever | [.toc](https://warcraft.wiki.gg/wiki/.toc) |
| Media files (textures, sounds, fonts) added after the client starts are never found | REPORTED | ForeverPlusPlus |
| SavedVariables are written only on `/reload`, logout or exit | CONFIRMED | standard client behaviour; `wowdev sv` prints file age for this reason |
| SavedVariables layout: `WTF\Account\<ACCOUNT>\SavedVariables\` (account-wide) and `WTF\Account\<ACCOUNT>\91\<Char>-<Realm>\SavedVariables\` (per character) | VERIFIED | this machine |
| Secret values are dropped from SavedVariables (become nil) | CONFIRMED | [Secret Values](https://warcraft.wiki.gg/wiki/Secret_Values) |
| Registering an event name the client does not know raises an error and stops the rest of the file | REPORTED | ForeverPlusPlus; `wowdev lint` checks event names for this reason |

## Lua environment

| Fact | Status | Source |
|---|---|---|
| Modified Lua 5.1; no `io` or `os`; `loadstring`, `getfenv`/`setfenv` and coroutines available | CONFIRMED; VERIFIED (build 70862, 2026-11-10, `lua.environment`): `_VERSION` is `"Lua 5.1"`, `io`/`os` nil, `loadstring`/`getfenv`/`setfenv` functions, `coroutine` a table | [Lua functions](https://warcraft.wiki.gg/wiki/Lua_functions) |
| Also absent: `load`, `debug`, `jit`, `utf8`, `bit32`. Present: `debugstack`, `debuglocals`, `securecall`, `issecurevariable`, `hooksecurefunc` | VERIFIED (build 70862, 2026-11-10, `lua.environment`) | probe result. Use `loadstring`, not `load`; `debugstack` instead of the `debug` library |
| `import(moduleName)` added in 1.60.1; it only references already-loaded modules | CONFIRMED; existence VERIFIED (build 70862, 2026-11-10, `lua.environment`) | [API import](https://warcraft.wiki.gg/wiki/API:import) |
| `require` was renamed to `import` (the API diff still lists `require` as added). **Answer: `import` is a function and `require` is nil at 70862**, so the rename note is right and the diff's "`require` added" is stale or beta-only | VERIFIED (build 70862, 2026-11-10, `lua.environment`); was PROBE PENDING | 1.60.1 notes vs API diff; probe result |
| No `goto`, no bitwise operators (use `bit`) | REPORTED; `bit` is a table VERIFIED (build 70862, 2026-11-10, `lua.environment`). `goto` and operators are syntax, not probed | wowforeverguides.com |
| `C_EncodingUtil.SerializeJSON` / `EncodeBase64` exist (useful for exports) | VERIFIED (build 70862, 2026-11-10, `lua.environment`); was PROBE PENDING | Atraeau/WoW-Addons uses them; probe result |

## API differences from Retail and Classic Era

| Fact | Status | Source |
|---|---|---|
| 176 global functions added, e.g. `C_GameRules.IsHardcoreActive`, `IsSelfFoundAllowed`, `Get/SetForeverExperiencePreset`, `UnitUsesAmmo`, `UnitResistance`, `GetArmorPenetration`, `GetHealthRegen`, `C_Spell.GetItemCooldown`, `C_DateAndTime.IsDayTime`, `ToggleSit`, `UIReload` | CONFIRMED (diff covers beta builds 70205-70425); the sampled names VERIFIED (build 70862, 2026-11-10, `api.foreverAdditions`, `client.gameRules`): all are functions, `ReloadUI` too. **Correction:** at 70862 there is no `C_GameRules.SetForeverExperiencePreset`; it is split into `SetForeverExperiencePresetPreLogin` and `SetForeverExperiencePresetPostLogin` (Blizzard source agrees: `GameRulesDocumentation.lua`) | [1.60.1 API changes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes); probe results |
| `C_SwingTimer` has only `EnableRangeCheck` and `IsTargetWithinSwingRange`; `C_DamageMeter` has `GetAvailableCombatSessions`, `GetCombatSessionFromID`, `GetCombatSessionFromType`, `GetCombatSessionSourceFromID`, `GetCombatSessionSourceFromType`, `GetSessionDurationSeconds`, `IsDamageMeterAvailable`, `ResetAllCombatSessions` | VERIFIED (build 70862, 2026-11-10, `api.foreverAdditions`) | probe result (function names only; not called) |
| Events added: `PLAYER_SWING`, `PLAYER_SWING_RANGE_UPDATE`, `UNIT_HAPPINESS`, `UNIT_PET_TRAINING_POINTS`, `PET_STATS_UPDATE`, `SHARD_TRANSFER`, `SHARD_TRANSFER_IMMINENT`, `GAME_RULES_CHANGED`, `DIEL_CYCLE_CHANGED` | CONFIRMED | 1.60.1 API changes |
| Classic-era globals are gone: `GetSpellInfo`, `GetItemInfo`, `UnitAura`, `GetContainerItemInfo`, `GetItemStats`. Use `C_Spell.GetSpellInfo`, `C_Item.GetItemInfo`, `C_UnitAuras.*`, `C_Container.*` | VERIFIED (build 70862, 2026-11-10, `api.removedGlobals`): all five bare globals are nil and `C_Item.GetItemInfo`, `C_Spell.GetSpellInfo`, `C_UnitAuras.GetAuraDataByIndex`, `C_Container.GetContainerItemInfo`, `C_Item.GetItemStats` are functions; earlier REPORTED, absent from Ketho's Forever global list (build 70532); `Blizzard_Deprecated` at 70862 defines no bare `GetSpellInfo` | ForeverPlusPlus; probe result (was PROBE PENDING) |
| `GetAddOnMetadata` is gone too; use `C_AddOns.GetAddOnMetadata` | VERIFIED (build 70862, 2026-11-10, `api.removedGlobals`) | probe result |
| `COMBAT_LOG_EVENT_UNFILTERED` never fires for addons; use `C_DamageMeter` or `UNIT_COMBAT` | CONFIRMED (Midnight rule) + REPORTED for Forever. Supporting: `CombatLogGetCurrentEventInfo` is nil, VERIFIED (build 70862, 2026-11-10, `api.removedGlobals`); the event itself was not tested | [12.0.0 API notes](https://warcraft.wiki.gg/wiki/Patch_12.0.0/API_changes/notes), ForeverPlusPlus |
| `C_PvP.IsWarModeFeatureEnabled` returns false; `GetInspectHonorData` returns two extra values | CONFIRMED | 1.60.1 API changes |
| The second value of `UnitName` is a surname, not a realm | VERIFIED (build 70862, 2026-11-10, `player`); was REPORTED. `UnitName("player")` returned two values; the second is a personal name-like word, not `nil` (retail's value for your own realm) and not the realm (`GetRealmName` differs). The test character has a surname set; a character without one was not tested | ForeverPlusPlus; probe result |
| **Player names are region-unique "First Last", with no realm part.** Forever's `NameUtil` (`Blizzard_FrameXMLUtil/Camelot/NameUtil.lua`) joins name and surname with `Constants.CharacterNameSeparatorConsts.CHARACTERNAME_SURNAME_SEPARATOR` = `" "`; `GetUnitName(unit, true)` (used by `/invite`) gives "First Last" without a realm; `RegionalUniqueNamesEnabled()` exists. `UNIT_SPELLCAST_SENT` target names and `GetPlayerInfoByGUID`'s name are "First Last" too, even for players from another realm. `GetPlayerInfoByGUID`'s realm value is the home realm, normalised (e.g. "CAMWOWUS2Realm2PvP"). `/targetexact First Last-Realm` found nobody (2026-11-10 field test), so do not append a realm to names used for targeting, emotes or whispers | VERIFIED in part (build 70862, 2026-11-10: log lines and the failed `-Realm` target); `/targetexact First Last` VERIFIED on the player's own character (2026-11-10, session 5: casts confirmed after targeting by full name) and on another player from another realm (2026-11-10, session 6: "cast confirmed" after the press tried "First Last") | vendor source; RRB field tests |
| `C_ChatInfo.PerformEmote(token, "First Last", true)` plays the emote **at another player with no key press** (out of combat, open world), unlike `SendChatMessage` "EMOTE", which needs one. It returned `false` even though the emote went out, so do not read the return value as success | VERIFIED (build 70862, 2026-11-10, RRB field test 3, session 6: "thank THANK at <player> ok true returned false"; the player saw the targeted emote in game) | RRB field test |
| `UnitUsesAmmo("player")` is true for a Rogue; `C_SpecializationInfo.GetSpecialization()` returns 1 at level 13; `UnitClass` / `UnitRace` / `UnitFactionGroup` return the retail-shaped tuples (`"Rogue", "ROGUE", 4`) | VERIFIED (build 70862, 2026-11-10, `player`) | probe result |
| New spec IDs (Warrior 1491); talents use `C_Traits` with config type `CamelotCombat` (4) | REPORTED | ForeverPlusPlus |
| Unit frames and minimap header were reskinned; skinning/anchoring code written for Retail breaks | CONFIRMED | 1.60.1 API change notes |
| Opening Blizzard windows from addon code taints them | REPORTED | ForeverPlusPlus |
| Edit Mode layouts are saved separately for mouse/keyboard and gamepad | CONFIRMED | 1.60.1 API change notes |

## Secret values (addon restrictions)

Blizzard: "The addon disarmament changes introduced in Midnight, including secrets, will also be
active in Forever" (CONFIRMED, quoted on the [1.60.1 API changes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes) page from a WoWUIDev Discord post of 2026-09-15).

A **secret value** is a value an addon can hold and display but not inspect: the game hands the
addon an opaque box instead of a number or string.

- **Restricted** (CONFIRMED, [12.0.0 notes](https://warcraft.wiki.gg/wiki/Patch_12.0.0/API_changes/notes)): combat log events are gone; cooldowns and auras are secret during M+, PvP matches, encounters and player combat; creature names and GUIDs are secret in instances; addon messages and chat reads are blocked during M+, PvP matches and encounters.
- **Forever-only extras** (CONFIRMED): a `FrameStrata` secret aspect; minimap rotation blocked during encounters; server and spawn IDs scrambled inside GUIDs (no layer detection); trial accounts cannot send addon messages; `SendChatMessage` "EMOTE" needs a hardware event.
- **Still readable**: the player's secondary resources, the player's own casts, `UnitHealthMax`/`UnitPowerMax` for player units, `UnitIsUnit` on target/focus/mouseover, whitelisted spells.
- **Allowed on a secret** (CONFIRMED, [Secret Values](https://warcraft.wiki.gg/wiki/Secret_Values)): store, pass on, concatenate, `string.format`, `type()`, hand to widgets (`FontString:SetText`, `StatusBar:SetValue`).
- **Errors on a secret**: arithmetic, comparison, `#`, use as a table key, indexing, `tonumber`, and a **true/false test** (`if x`, `not x`, `x and y`): VERIFIED (build 70862, 2026-11-10, RRB field test 3: "attempt to perform boolean test on field 'isFullUpdate' (a secret boolean value)"). Put the guard first: `if not canaccessvalue(x) or not x`, never `if not x or not canaccessvalue(x)`. The linters do not catch the wrong order.
- **A readable table can hold hidden fields** (VERIFIED, same error): `UNIT_AURA`'s `updateInfo` was an ordinary table while its `isFullUpdate` was a secret boolean and `removedAuraInstanceIDs` a secret table, out of combat while grouped (3 times in a ~4 h session). Check each field before testing it, and check tables with `canaccesstable` before reading inside them, as Blizzard's `Dump.lua` does (`not canaccessvalue(v) or (type(v) == "table" and not canaccesstable(v))`).
- **Helper API**: `issecretvalue`, `canaccessvalue`, `issecrettable`, `canaccesstable`, `C_Secrets`, `C_RestrictedActions`, `C_CurveUtil`, `C_DurationUtil`, `StatusBar:SetTimerDuration`. Existence VERIFIED (build 70862, 2026-11-10, `api.secrets`) for all but `StatusBar:SetTimerDuration` (a widget method, not probed); was PROBE PENDING. Also present: `scrubsecretvalues`, `canaccesssecrets`, `secretwrap`, `hasanysecretvalues`, `dropsecretaccess`, and `EVENTTRACE_SECRET_*` strings.
  - `C_RestrictedActions` (3): `CheckAllowProtectedFunctions`, `GetAddOnRestrictionState`, `IsAddOnRestrictionActive`.
  - `C_Secrets` (27): `CanCompareUnitTokens`, `HasSecretRestrictions`, `Get{PowerType,SpellAura,SpellCast,SpellCooldown}Secrecy`, and `Should...BeSecret` for `ActionCooldown`, `Auras`, `Cooldowns`, `SpellAura`, `SpellBookItemCooldown`, `SpellCooldown`, `TotemSlot`, `TotemSpell`, `UnitAuraIndex`, `UnitAuraInstance`, `UnitAuraSlot`, `UnitComparison`, `UnitHealthMax`, `UnitIdentity`, `UnitPower`, `UnitPowerMax`, `UnitSpellCast`, `UnitSpellCasting`, `UnitStats`, `UnitThreatState`, `UnitThreatValues`.
  - `C_Secrets.ShouldAurasBeSecret()` returned false at login, out of combat.
- **Effectively impossible now**: combat-log damage meters and parsers, WeakAuras-style logic on auras or cooldowns in combat, health-math healing addons, threat arithmetic, comms-based raid tools inside instances.
- The player's own mana is secret even out of combat | REPORTED (ForeverPlusPlus). **Probe result, partly answering it** (build 70862, 2026-11-10, `api.secrets.playerValueIsSecret`): at `PLAYER_LOGIN`, out of combat in the open world, `issecretvalue` was **true for `UnitHealth("player")` and `UnitPower("player")`** and false for `UnitHealthMax`, `UnitPowerMax`, `UnitName` and `UnitGUID` (all `"player"`). VERIFIED for that moment only. Two limits: (1) the test character is a Rogue, so `UnitPower` read energy, not mana; mana itself is unprobed, but the result suggests primary power in general is secret; (2) the probe runs only at login, so whether these stay secret later in a normal out-of-combat session is unproven. Re-run `/dvb probe` mid-session (out of combat) to settle it. **Re-checked at login on build 70888** (2026-11-11, session 10, 9 s after a `/reload`): same answer, health and power secret, the other four readable. That is still a login-time reading: a mid-session `/dvb probe` (end of a two-hour session 9) was replaced by the automatic run at the next login before a second `/reload` saved the file. Since 2026-11-11 DevBridge keeps a typed `/dvb probe` as `manualProbes`, which later logins do not replace. Until then, treat the player's current health and power as possibly secret everywhere: guard with `issecretvalue` and hand them to widgets rather than doing maths. Consistent with "Still readable" above: `UnitHealthMax`/`UnitPowerMax` were readable.
- Check `C_Secrets.ShouldAurasBeSecret()` before reading auras in combat | REPORTED (ForeverPlusPlus); the function exists, VERIFIED (build 70862, 2026-11-10, `api.secrets`).
- Restriction-testing CVar names: `addon*RestrictionsForced` (Secret Values page) vs `secret*RestrictionsForced` (12.0.0 notes). **Answer: `addon*RestrictionsForced`.** VERIFIED (build 70862, 2026-11-10, `console.devCommands`); was PROBE PENDING. The console lists 1877 commands; those matching "restrict"/"secret" include `addonChatRestrictionsForced`, `addonCombatRestrictionsForced`, `addonEncounterRestrictionsForced`, `addonChallengeModeRestrictionsForced`, `addonPvPMatchRestrictionsForced`, `addonMapRestrictionsForced` (all "0"), and no `secret*RestrictionsForced`. The filter did match other "secret" names (`taintLogObjectSecrets`), so the absence is real for the commands `C_Console.GetAllCommands` lists; hidden commands, if any exist, would not show.
- Other dev CVars seen (`console.devCommands`, VERIFIED build 70862, 2026-11-10): `scriptErrors` 0, `scriptWarnings` 0, `scriptProfile` 0, `taintLog` 0 (levels 0-4), `taintLogObjectSecrets` 0, `addonLoadDebugging` 0 (1 writes `AddOnLoad.log`), `checkAddonVersion` 1, `disableUserAddonsByDefault` 0, `addonPerformanceMsg{Warning,Error,Overall}` 0.

## Developer tools in the client

| Tool | Status | Notes |
|---|---|---|
| `/dump expr`, `/tinspect`, `/fstack`, `/reload` | CONFIRMED (pages last edited 2022) | [DevTools](https://warcraft.wiki.gg/wiki/DevTools_AddOn), [framestack](https://warcraft.wiki.gg/wiki/MACRO_framestack) |
| `/etrace` (event trace; shows secret state since 12.0.0) | REPORTED + CONFIRMED | 12.0.0 notes |
| Console: add `-console` to Battle.net launch arguments, press the backtick key | CONFIRMED | [Console](https://warcraft.wiki.gg/wiki/Console) |
| `ExportInterfaceFiles code` at login screen | CONFIRMED for retail, untested on Forever | not needed: Gethe's `forever` branch matches our build exactly |
| `/api` in-game API browser (Blizzard_APIDocumentation) | exists in the 70862 source | `tools/vendor/wow-ui-source/Interface/AddOns/Blizzard_APIDocumentation` |
| `C_AddOns.GetNumAddOns`, `GetAddOnInfo`, `IsAddOnLoaded`, `GetAddOnMetadata` work as on retail; our addons report security `INSECURE` and `IsAddOnLoaded` returns two values (`true, true`) | VERIFIED (build 70862, 2026-11-10, `addons.list`) | probe result: all five installed addons (four of ours, one third-party) loaded |

## Publishing (for later)

- CurseForge shows Forever files as game version "1.60.1", flavor "Forever"; the BigWigs packager uses CurseForge game-version type 88568. REPORTED.
- BigWigs packager v2.7.0 (2026-11-05) supports Forever by interface number (16xxx); `_Forever`/`_Camelot` TOC-file detection is unclear. REPORTED, [packager releases](https://github.com/BigWigsMods/packager/releases).
- Working example: `cjber/adventure-guide-forever` (single TOC, `## Interface: 16001`, packager pinned by SHA). REPORTED.

## Contradictions register

| # | Claim A | Claim B | Settles it | Status |
|---|---|---|---|---|
| 1 | `WOW_PROJECT_ID == 1` (ForeverPlusPlus, in game) | `== 18` (fan site, build 70338) | probe `client.projectId` | **SETTLED 2026-11-10: B.** 18 (`WOW_PROJECT_CAMELOT`) at build 70862 |
| 2 | `_Forever` TOC suffix works (wiki) | packager and two dev setups use `_Camelot` only | probe `toc.suffixWinner` | **SETTLED 2026-11-10: both work; `_Camelot` wins.** Order `_Camelot` > `_Forever` > plain |
| 3 | `require` renamed to `import` (notes) | `require` listed as added (API diff) | probe `lua.environment` | **SETTLED 2026-11-10: A.** `import` exists, `require` is nil |
| 4 | `addon*RestrictionsForced` CVars | `secret*RestrictionsForced` CVars | probe `console.devCommands` | **SETTLED 2026-11-10: A.** Six `addon*RestrictionsForced` CVars, no `secret*` ones |
| 5 | secure snippets fail on 69913 (fan site) | work on 70009 (ForeverPlusPlus) | not probed yet | open |
| 6 | restrictions apply in plain combat (wiki) | only arenas, battlegrounds, encounters (CurseForge addon page) | not probed yet; needs an in-combat test | open. New evidence against B: player `UnitHealth`/`UnitPower` were secret at login out of combat in the open world (`api.secrets`), so some secrecy applies outside instances entirely |

## Sources

- Warcraft Wiki: [Patch 1.60.1/API changes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes), [notes](https://warcraft.wiki.gg/wiki/Patch_1.60.1/API_changes/notes), [TOC format](https://warcraft.wiki.gg/wiki/TOC_format), [Secret Values](https://warcraft.wiki.gg/wiki/Secret_Values), [Patch 12.0.0 notes](https://warcraft.wiki.gg/wiki/Patch_12.0.0/API_changes/notes), [Lua functions](https://warcraft.wiki.gg/wiki/Lua_functions)
- Developer repos: [xIGBClutchIx/ForeverPlusPlus](https://github.com/xIGBClutchIx/ForeverPlusPlus) (`docs/forever-api.md` is the best community fact file), [Atraeau/WoW-Addons](https://github.com/Atraeau/WoW-Addons)
- Blizzard source mirror: [Gethe/wow-ui-source `forever`](https://github.com/Gethe/wow-ui-source/tree/forever) (cloned to `tools/vendor/wow-ui-source`, build 70862)
- Fan sites (lowest weight): [wowforeverguides.com developer guide](https://wowforeverguides.com/addons/developers), [API cheat sheet](https://wowforeverguides.com/addons/api-cheatsheet)
