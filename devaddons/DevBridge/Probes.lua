-- Probes: small checks of what this Forever client's API really does.
-- Each probe runs at PLAYER_LOGIN (and on /dvb probe); its return value is
-- stored in DevBridgeDB.probes[name]. Several exist to settle facts that
-- published sources disagree on (see docs/wiki/research/forever-addon-api.md).

local DevBridge = DevBridge
local Call = DevBridge.Call
local Resolve = DevBridge.Resolve
local probe = DevBridge.RegisterProbe

local function kind(path)
  return type(Resolve(path))
end

-- Returns { [path] = type } for a list of dotted paths.
local function kinds(paths)
  local out = {}
  for _, path in ipairs(paths) do
    out[path] = kind(path)
  end
  return out
end

local function functionNames(tbl)
  local names = {}
  if type(tbl) == "table" then
    for k, v in pairs(tbl) do
      if type(k) == "string" and type(v) == "function" then
        names[#names + 1] = k
      end
    end
    table.sort(names)
  end
  return names
end

probe("client.build", function()
  local version, build, date, interface, localized, buildType = GetBuildInfo()
  return {
    version = version, build = build, date = date, interface = interface,
    localizedVersion = localized, buildType = buildType,
  }
end)

-- Sources disagree: WOW_PROJECT_ID reported as 1 (Mainline) and as 18 (Camelot).
probe("client.projectId", function()
  local out = { WOW_PROJECT_ID = WOW_PROJECT_ID }
  for k, v in pairs(_G) do
    if type(k) == "string" and k:match("^WOW_PROJECT_") then
      out[k] = v
    end
  end
  return out
end)

probe("client.gameRules", function()
  return {
    namespaceFunctions = functionNames(C_GameRules),
    IsHardcoreActive = Call("C_GameRules.IsHardcoreActive"),
    IsSelfFoundAllowed = Call("C_GameRules.IsSelfFoundAllowed"),
    GetForeverExperiencePreset = Call("C_GameRules.GetForeverExperiencePreset"),
    GetActiveGameMode = Call("C_GameRules.GetActiveGameMode"),
  }
end)

probe("client.environment", function()
  return {
    locale = Call("GetLocale"),
    region = Call("GetCurrentRegion"),
    regionName = Call("GetCurrentRegionName"),
    realm = Call("GetRealmName"),
    isTestBuild = Call("IsTestBuild"),
    isPublicBuild = Call("IsPublicBuild"),
    expansionLevel = Call("GetExpansionLevel"),
    serverExpansionLevel = Call("GetServerExpansionLevel"),
    maxPlayerLevel = Call("GetMaxPlayerLevel"),
    maxLevelForExpansion = Call("GetMaxLevelForPlayerExpansion"),
    LE_EXPANSION_LEVEL_CURRENT = LE_EXPANSION_LEVEL_CURRENT,
    inCombat = Call("InCombatLockdown"),
  }
end)

probe("player", function()
  return {
    -- The second UnitName value is reported to be a surname on Forever, not a realm.
    name = Call("UnitName", "player"),
    level = Call("UnitLevel", "player"),
    class = Call("UnitClass", "player"),
    race = Call("UnitRace", "player"),
    faction = Call("UnitFactionGroup", "player"),
    usesAmmo = Call("UnitUsesAmmo", "player"),
    specialization = Call("C_SpecializationInfo.GetSpecialization"),
  }
end)

-- Sources disagree: 1.60.1 notes say `require` was renamed to `import`.
probe("lua.environment", function()
  return {
    version = _VERSION,
    types = kinds({
      "loadstring", "load", "setfenv", "getfenv", "require", "import",
      "coroutine", "bit", "bit32", "utf8", "jit", "io", "os", "debug",
      "debugstack", "debuglocals", "securecall", "issecurevariable",
      "hooksecurefunc", "C_EncodingUtil", "C_EncodingUtil.SerializeJSON",
      "C_EncodingUtil.EncodeBase64",
    }),
  }
end)

-- Classic-era globals reported removed in Forever, and their C_ replacements.
probe("api.removedGlobals", function()
  return kinds({
    "GetItemInfo", "C_Item.GetItemInfo",
    "GetSpellInfo", "C_Spell.GetSpellInfo",
    "UnitAura", "C_UnitAuras.GetAuraDataByIndex",
    "GetContainerItemInfo", "C_Container.GetContainerItemInfo",
    "GetItemStats", "C_Item.GetItemStats",
    "GetAddOnMetadata", "C_AddOns.GetAddOnMetadata",
    "CombatLogGetCurrentEventInfo",
  })
end)

-- Functions the 1.60.1 API diff lists as added for Forever.
probe("api.foreverAdditions", function()
  return {
    types = kinds({
      "UIReload", "ReloadUI", "ToggleSit", "UnitUsesAmmo", "UnitResistance",
      "GetArmorPenetration", "GetHealthRegen", "C_Spell.GetItemCooldown",
      "C_DateAndTime.IsDayTime", "C_SwingTimer", "C_DamageMeter",
    }),
    swingTimerFunctions = functionNames(C_SwingTimer),
    damageMeterFunctions = functionNames(C_DamageMeter),
  }
end)

-- Secret values: what exists, and what is secret right now (out of combat).
probe("api.secrets", function()
  local out = {
    types = kinds({
      "issecretvalue", "canaccessvalue", "issecrettable", "canaccesstable",
      "scrubsecretvalues", "C_Secrets", "C_RestrictedActions",
      "C_CurveUtil", "C_DurationUtil",
    }),
    secretsFunctions = functionNames(C_Secrets),
    restrictedActionsFunctions = functionNames(C_RestrictedActions),
    shouldAurasBeSecret = Call("C_Secrets.ShouldAurasBeSecret"),
    globalsMentioningSecret = {},
  }
  for k, v in pairs(_G) do
    if type(k) == "string" and k:lower():find("secret", 1, true) then
      out.globalsMentioningSecret[k] = type(v)
    end
  end
  local isSecret = _G.issecretvalue
  if isSecret then
    local checks = {
      UnitHealth = function() return UnitHealth("player") end,
      UnitHealthMax = function() return UnitHealthMax("player") end,
      UnitPower = function() return UnitPower("player") end,
      UnitPowerMax = function() return UnitPowerMax("player") end,
      UnitName = function() return UnitName("player") end,
      UnitGUID = function() return UnitGUID("player") end,
    }
    out.playerValueIsSecret = {}
    for label, fn in pairs(checks) do
      local ok, value = pcall(fn)
      if ok then
        out.playerValueIsSecret[label] = isSecret(value) and true or false
      else
        out.playerValueIsSecret[label] = "error: " .. tostring(value)
      end
    end
  end
  return out
end)

-- Console variables and commands about scripts, taint and restrictions.
-- Sources disagree on the restriction-testing CVar names
-- (addon*RestrictionsForced vs secret*RestrictionsForced), so list them all.
probe("console.devCommands", function()
  local getAll = Resolve("C_Console.GetAllCommands") or Resolve("ConsoleGetAllCommands")
  if type(getAll) ~= "function" then
    return "<no command listing API>"
  end
  local matches = {}
  local total = 0
  for _, info in ipairs(getAll() or {}) do
    total = total + 1
    local name = info.command or info.name
    if type(name) == "string" then
      local lower = name:lower()
      if lower:find("restrict", 1, true) or lower:find("secret", 1, true)
        or lower:find("script", 1, true) or lower:find("taint", 1, true)
        or lower:find("addon", 1, true) or lower:find("export", 1, true) then
        local value = Call("C_CVar.GetCVar", name)
        matches[name] = {
          help = info.help,
          commandType = info.commandType,
          value = value.status == "ok" and value.values[1] or nil,
        }
      end
    end
  end
  return { totalCommands = total, matches = matches }
end)

probe("addons.list", function()
  local out = {}
  local count = Call("C_AddOns.GetNumAddOns")
  local n = count.status == "ok" and count.values[1] or 0
  for i = 1, n do
    local name, title, _, loadable, reason, security = C_AddOns.GetAddOnInfo(i)
    local entry = {
      name = name,
      title = title,
      loadable = loadable,
      reason = reason,
      security = security,
    }
    if name then
      entry.loaded = Call("C_AddOns.IsAddOnLoaded", name)
      entry.version = Call("C_AddOns.GetAddOnMetadata", name, "Version")
    end
    out[#out + 1] = entry
  end
  return out
end)
