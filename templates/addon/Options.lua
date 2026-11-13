-- The settings page: Options > AddOns > __TITLE__, built with Blizzard's
-- settings API (Blizzard_Settings_Shared). Each setting writes straight into
-- the saved table (ns.db). For sliders, dropdowns, buttons, section headers
-- and settings that grey out under a parent tickbox, look up
-- Settings.CreateSlider, Settings.CreateDropdown and friends in
-- Blizzard_Settings_Shared (tools/vendor/wow-ui-source).

local ADDON_NAME, ns = ...

local category
local registered = {} -- every setting on the page, for SyncOptions

-- Registers db[key] as a setting; its type and default come from ns.defaults.
local function addSetting(db, key, name)
  local default = ns.defaults[key]
  local setting = Settings.RegisterAddOnSetting(category, ADDON_NAME .. "_" .. key, key, db,
    type(default), name, default)
  -- To react when a setting changes (on this page, or after SyncOptions):
  -- setting:SetValueChangedCallback(function(_, value) ... end)
  registered[#registered + 1] = setting
  return setting
end

function ns.BuildOptions(db)
  if not (Settings and Settings.RegisterVerticalLayoutCategory) then
    return
  end
  category = Settings.RegisterVerticalLayoutCategory("__TITLE__")

  Settings.CreateCheckbox(category, addSetting(db, "enabled", "Enabled"),
    "Turns __TITLE__ on or off. /__LOWERNAME__ toggle does the same.")

  Settings.RegisterAddOnCategory(category)
end

-- /__LOWERNAME__ options
function ns.OpenOptions()
  if not category then
    ns.say("the settings page is not available; look under Options > AddOns.")
    return
  end
  -- Opening the panel is a restricted call; a blocked call in combat would not
  -- be caught by pcall, so do not try.
  if InCombatLockdown() then
    ns.say("the settings open after combat; try again then.")
    return
  end
  if not pcall(Settings.OpenToCategory, category:GetID()) then
    ns.say("could not open the settings page; look under Options > AddOns.")
  end
end

-- Slash commands change the same values; tell the page to show them.
function ns.SyncOptions()
  for _, setting in ipairs(registered) do
    setting:NotifyUpdate()
  end
end
