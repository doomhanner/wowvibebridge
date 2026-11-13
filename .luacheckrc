-- luacheck config for WoW: Forever addons.
--
-- Division of labour: luacheck covers plain Lua hygiene (accidental globals,
-- unused variables, shadowing, unreachable code, syntax errors). Whether a
-- WoW API exists on Forever, and is called correctly, is wowlua-ls's and
-- apicheck's job, so reading globals luacheck does not know is allowed.

std = "lua51"
max_line_length = false
exclude_files = { "tools/**", "templates/**", "data/**" }

ignore = {
  "113",      -- accessing an undefined global: wowlua-ls and apicheck check the WoW API
  "212/self", -- unused self argument in methods
}

-- Blizzard globals that addons legitimately write to.
globals = {
  "SlashCmdList",
}

-- Each addon declares the globals it owns (its SavedVariables, slash command
-- names and any deliberate public table). Anything else it writes to _G is
-- reported as an accidental global (W111). `wowdev new` adds a line here for
-- every addon it creates.
files["devaddons/DevBridge/**"] = {
  globals = { "DevBridge", "DevBridgeDB", "SLASH_DEVBRIDGE1", "SLASH_DEVBRIDGE2" },
}
