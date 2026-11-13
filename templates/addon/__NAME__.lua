-- __TITLE__
-- Scaffolded by tools/wowdev.py new. Target: WoW Forever (interface __INTERFACE__).
-- Settings page: Options.lua (Options > AddOns > __TITLE__, or /__LOWERNAME__ options).

local ADDON_NAME, ns = ...

-- Settings saved between sessions. New keys added here get their default the
-- next time the addon loads; existing saved values are kept.
local defaults = {
  enabled = true,
}
ns.defaults = defaults -- the settings page reads each setting's default here

-- Debug lines go to DevBridge when it is installed, and are read outside the
-- game with: python tools/wowdev.py sv DevBridge --var DevBridgeDB
local function log(...)
  local bridge = _G.DevBridge -- optional dependency; nil when not installed
  if bridge then
    bridge.Log(ADDON_NAME, ...)
  end
end
ns.log = log

local function say(text)
  print("|cff66ccff__TITLE__|r: " .. text)
end
ns.say = say

-- Runs fn inside pcall so one bad event payload cannot break the addon. The
-- error still goes to the game's error handler, so DevBridge records it.
local function guarded(fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then
    geterrorhandler()(err)
  end
end
ns.guarded = guarded

-- Hidden ("secret") game values. In combat, in instances and sometimes
-- outside them, the game hands addons opaque values for things like auras,
-- unit names, GUIDs, health and power. Storing one, passing it on or showing
-- it in a widget is fine; a true/false test (if x, not x, x and y), a
-- comparison, maths, # or indexing is an error. Check first, and call
-- canaccessvalue directly (not through a helper) so wowlua-ls sees the guard:
--
--   local guid = UnitGUID(unit)
--   if not canaccessvalue(guid) or not guid then return end
--   -- never "if not guid or not canaccessvalue(guid)": that tests it first
--
-- A readable table can still hold hidden fields. Check the table before
-- reading inside it, then check each field before testing it:
--
--   if not canaccessvalue(aura) or type(aura) ~= "table" or not canaccesstable(aura) then return end
--   local name = aura.name
--   if not canaccessvalue(name) or not name then return end
--
-- More: docs/wiki/research/forever-addon-api.md, "Secret values".

local frame = CreateFrame("Frame")

-- One handler per event: handlers.EVENT_NAME receives the event's arguments.
-- Every handler runs through guarded(), so an error in one is recorded and
-- the addon keeps working.
local handlers = {}

function handlers.ADDON_LOADED(name)
  if name ~= ADDON_NAME then return end
  __NAME__DB = __NAME__DB or {}
  for key, value in pairs(defaults) do
    if __NAME__DB[key] == nil then
      __NAME__DB[key] = value
    end
  end
  ns.db = __NAME__DB
  frame:UnregisterEvent("ADDON_LOADED")
  -- The settings page (Options.lua); a failure there must not stop the addon.
  guarded(ns.BuildOptions, ns.db)
end

function handlers.PLAYER_LOGIN()
  log("loaded, enabled =", ns.db.enabled)
end

-- Registering an event name the client does not know raises an error and
-- stops this file, so only add events confirmed to exist on Forever
-- (see tools/vendor/BlizzardInterfaceResources/Resources/Events.lua). Keep
-- the names literal: wowdev lint checks them.
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")

frame:SetScript("OnEvent", function(_, event, ...)
  local handler = handlers[event]
  if handler then
    guarded(handler, ...)
  end
end)

SLASH___UPPERNAME__1 = "/__LOWERNAME__"
SlashCmdList.__UPPERNAME__ = function(msg)
  if not ns.db then
    return -- saved settings not loaded yet
  end
  local command = (msg or ""):match("^%s*(%S*)"):lower()
  if command == "options" or command == "config" or command == "settings" then
    if ns.OpenOptions then
      ns.OpenOptions()
    end
  elseif command == "toggle" then
    ns.db.enabled = not ns.db.enabled
    if ns.SyncOptions then
      ns.SyncOptions() -- show the change on the settings page
    end
    say("enabled = " .. tostring(ns.db.enabled))
  else
    say("commands: /__LOWERNAME__ options | toggle")
  end
end
