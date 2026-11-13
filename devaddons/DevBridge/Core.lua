-- DevBridge: development helper for this project's addons.
--
-- Records Lua errors, blocked-action warnings, debug log lines and probe
-- results into the DevBridgeDB SavedVariable. The game only writes that file
-- on /reload, logout or exit; tools/wowdev.py reads it outside the game.
--
-- Other addons in this project can call (always guard with `if DevBridge`):
--   DevBridge.Log(source, ...)            -- append a debug line
--   DevBridge.RegisterProbe(name, fn)     -- run fn at login, store its result
--   DevBridge.Call("C_Foo.Bar", ...)      -- safe call by dotted path

local ADDON_NAME, ns = ...

local MAX_ERRORS = 200
local MAX_LOG = 500
local MAX_STRING = 4000
local MAX_DEPTH = 8
local DB_VERSION = 1

local DevBridge = {}
_G.DevBridge = DevBridge
ns.DevBridge = DevBridge

local db                 -- DevBridgeDB once SavedVariables have loaded
local sessionId = 0      -- increments every load (/reload counts as a new session)
local pendingErrors = {} -- captured before SavedVariables were available
local pendingLog = {}
local probes = {}        -- ordered list of { name = string, fn = function }

-- Secret values (Midnight-era addon restrictions, also active in Forever) error
-- on comparison, arithmetic and indexing, so every unknown value is checked
-- with these before it is touched. Both are nil on clients without secrets.
local isSecret = _G.issecretvalue
local isSecretTable = _G.issecrettable

local function chat(text)
  print("|cff33ff99DevBridge|r: " .. text)
end
DevBridge.Print = chat

local function pack(...)
  return { n = select("#", ...), ... }
end

---@param s any
---@return string
local function truncate(s)
  if isSecret and isSecret(s) then
    return "<secret>"
  end
  if type(s) ~= "string" then
    return "<" .. type(s) .. ">"
  end
  if #s > MAX_STRING then
    return s:sub(1, MAX_STRING) .. "...(truncated)"
  end
  return s
end

local function safeToString(value)
  if isSecret and isSecret(value) then
    return "<secret>"
  end
  local ok, s = pcall(tostring, value)
  if ok and type(s) == "string" then
    return truncate(s)
  end
  return "<unprintable>"
end

-- Turns any value into something SavedVariables can store: functions, widgets,
-- secret values, cycles and very deep tables become short descriptive strings.
local function sanitize(value, depth, seen)
  if isSecret and isSecret(value) then
    return "<secret>"
  end
  local t = type(value)
  if t == "string" then
    return truncate(value)
  elseif t == "number" then
    if value ~= value then return "nan" end
    if value == math.huge then return "inf" end
    if value == -math.huge then return "-inf" end
    return value
  elseif t == "boolean" or t == "nil" then
    return value
  elseif t == "table" then
    seen = seen or {}
    depth = depth or 0
    if isSecretTable and isSecretTable(value) then return "<secret table>" end
    if seen[value] then return "<cycle>" end
    if depth >= MAX_DEPTH then return "<max depth>" end
    if type(rawget(value, 0)) == "userdata" then
      local name
      local okGet, getName = pcall(function() return value.GetName end)
      if okGet and type(getName) == "function" then
        local ok, n = pcall(getName, value)
        if ok and not (isSecret and isSecret(n)) then name = n end
      end
      return "<widget " .. tostring(name or "anonymous") .. ">"
    end
    seen[value] = true
    local out = {}
    for k, v in pairs(value) do
      local kt = type(k)
      if (kt == "string" or kt == "number" or kt == "boolean") and not (isSecret and isSecret(k)) then
        out[k] = sanitize(v, depth + 1, seen)
      end
    end
    seen[value] = nil
    return out
  end
  return "<" .. t .. ">"
end
DevBridge.Sanitize = sanitize

-- Looks up a dotted global path such as "C_AddOns.GetNumAddOns".
local function resolve(path)
  local v = _G
  for part in path:gmatch("[^%.]+") do
    if type(v) ~= "table" then return nil end
    v = v[part]
  end
  return v
end
DevBridge.Resolve = resolve

-- Calls a function by dotted path without ever raising. Returns a table:
--   { status = "missing" } | { status = "ok", values = {...}, n = count }
--   | { status = "error", error = message }
function DevBridge.Call(path, ...)
  local fn = resolve(path)
  if type(fn) ~= "function" then
    return { status = "missing" }
  end
  local results = pack(pcall(fn, ...))
  if not results[1] then
    return { status = "error", error = safeToString(results[2]) }
  end
  local values = {}
  for i = 2, results.n do
    local ok, clean = pcall(sanitize, results[i])
    if not ok then
      values[i - 1] = "<unstorable: " .. safeToString(clean) .. ">"
    elseif clean == nil then
      values[i - 1] = "<nil>" -- a hole in the list would vanish from SavedVariables
    else
      values[i - 1] = clean -- false is kept as false
    end
  end
  return { status = "ok", values = values, n = results.n - 1 }
end

---------------------------------------------------------------------------
-- Error capture
---------------------------------------------------------------------------

local function storeError(entry)
  local list = db.errors
  local oldest = math.max(1, #list - 50)
  for i = #list, oldest, -1 do
    local e = list[i]
    if e.kind == entry.kind and e.message == entry.message then
      e.count = (e.count or 1) + 1
      e.lastTime = entry.time
      e.lastSession = entry.session
      return
    end
  end
  list[#list + 1] = entry
  while #list > MAX_ERRORS do
    table.remove(list, 1)
  end
end

local function recordError(kind, message, stack, locals)
  local entry = {
    kind = kind,
    message = safeToString(message),
    stack = stack and truncate(stack) or nil,
    locals = locals and truncate(locals) or nil,
    time = time(),
    session = sessionId,
    count = 1,
  }
  if db then
    storeError(entry)
  else
    pendingErrors[#pendingErrors + 1] = entry
  end
end
DevBridge.RecordError = recordError

-- Stack and locals of the frame that raised the error. Same level maths as
-- Blizzard's own handler (Blizzard_ScriptErrors.lua, GetErrorData): measure
-- the stack height here, in the same function that calls debugstack, then
-- subtract the height the stack had when the error happened.
local function captureErrorContext()
  local current = GetCallstackHeight()
  local errorHeight = GetErrorCallstackHeight and GetErrorCallstackHeight()
  local level = current - ((errorHeight and errorHeight - 1) or 0)
  return debugstack(level), debuglocals(level)
end

local previousHandler = geterrorhandler()
local inHandler = false
seterrorhandler(function(message, ...)
  if not inHandler then
    inHandler = true
    -- Everything here is guarded: if anything raised, inHandler would stay
    -- true and every later error would go unrecorded.
    local ok, stack, locals = pcall(captureErrorContext)
    if not ok then
      stack, locals = "(stack capture failed: " .. safeToString(stack) .. ")", nil
    end
    pcall(recordError, "error", message, stack, locals)
    inHandler = false
  end
  if previousHandler then
    return previousHandler(message, ...)
  end
end)

---------------------------------------------------------------------------
-- Debug log
---------------------------------------------------------------------------

function DevBridge.Log(source, ...)
  local parts = {}
  for i = 1, select("#", ...) do
    parts[#parts + 1] = safeToString((select(i, ...)))
  end
  local line = {
    time = time(),
    session = sessionId,
    source = safeToString(source),
    text = truncate(table.concat(parts, " ")),
  }
  if db then
    local log = db.log
    log[#log + 1] = line
    while #log > MAX_LOG do
      table.remove(log, 1)
    end
  else
    pendingLog[#pendingLog + 1] = line
  end
end

---------------------------------------------------------------------------
-- Probes: small checks of what this client's API really does
---------------------------------------------------------------------------

function DevBridge.RegisterProbe(name, fn)
  probes[#probes + 1] = { name = name, fn = fn }
end

function DevBridge.RunProbes()
  if not db then return 0, 0 end
  local results = {}
  local failed = 0
  for _, probe in ipairs(probes) do
    local r = pack(pcall(probe.fn))
    local cleanOk, clean
    if r[1] then
      cleanOk, clean = pcall(sanitize, r[2])
      if not cleanOk then
        r[1], r[2] = false, "result could not be stored: " .. safeToString(clean)
      end
    end
    if r[1] then
      results[probe.name] = { ok = true, result = clean, session = sessionId }
    else
      failed = failed + 1
      results[probe.name] = { ok = false, error = safeToString(r[2]), session = sessionId }
    end
  end
  db.probes = results
  db.probesRanAt = time()
  return #probes, failed
end

---------------------------------------------------------------------------
-- API inventory: every global function and C_ namespace this client exposes
---------------------------------------------------------------------------

-- True when a global was set by Blizzard code, false when an addon set or
-- replaced it (issecurevariable reports the tainting addon). Addon globals
-- must not count as "the client has this API".
local function isBlizzardGlobal(key)
  if type(issecurevariable) ~= "function" then return true end
  local ok, secure = pcall(issecurevariable, _G, key)
  return ok and secure and true or false
end

function DevBridge.TakeApiInventory()
  local globals, namespaces, enumNames = {}, {}, {}
  local namespaceCount, namespacedFunctions = 0, 0
  local addonOwned = 0
  for key, value in pairs(_G) do
    if type(key) == "string" and not isBlizzardGlobal(key) then
      addonOwned = addonOwned + 1
    elseif type(key) == "string" then
      local t = type(value)
      if t == "function" then
        globals[#globals + 1] = key
      elseif t == "table" and key:match("^C_") then
        local fns = {}
        for fk, fv in pairs(value) do
          if type(fk) == "string" and type(fv) == "function" then
            fns[#fns + 1] = fk
          end
        end
        table.sort(fns)
        namespaces[key] = fns
        namespaceCount = namespaceCount + 1
        namespacedFunctions = namespacedFunctions + #fns
      end
    end
  end
  table.sort(globals)
  if type(Enum) == "table" then
    for key in pairs(Enum) do
      if type(key) == "string" then
        enumNames[#enumNames + 1] = key
      end
    end
    table.sort(enumNames)
  end
  local version, build, _, interface = GetBuildInfo()
  db.api = {
    takenAt = time(),
    version = version,
    build = build,
    interface = interface,
    globals = globals,
    namespaces = namespaces,
    enumNames = enumNames,
    -- Globals skipped because an addon (not Blizzard) set them.
    addonOwnedSkipped = addonOwned,
  }
  return #globals, namespaceCount, namespacedFunctions
end

---------------------------------------------------------------------------
-- Startup
---------------------------------------------------------------------------

local function initDatabase()
  DevBridgeDB = DevBridgeDB or {}
  db = DevBridgeDB
  if db.version ~= DB_VERSION then
    db.version = DB_VERSION
  end
  db.errors = db.errors or {}
  db.log = db.log or {}
  db.sessionCounter = (db.sessionCounter or 0) + 1
  sessionId = db.sessionCounter
  local version, build, _, interface = GetBuildInfo()
  db.session = {
    id = sessionId,
    started = time(),
    version = version,
    build = build,
    interface = interface,
  }
  for _, entry in ipairs(pendingErrors) do
    entry.session = sessionId
    storeError(entry)
  end
  pendingErrors = {}
  for _, line in ipairs(pendingLog) do
    line.session = sessionId
    db.log[#db.log + 1] = line
  end
  pendingLog = {}
end

local frame = CreateFrame("Frame")

local function tryRegister(event)
  -- Unknown event names raise an error on modern clients, so register safely.
  return pcall(frame.RegisterEvent, frame, event)
end

tryRegister("ADDON_LOADED")
tryRegister("PLAYER_LOGIN")
for _, event in ipairs({
  "ADDON_ACTION_BLOCKED", "ADDON_ACTION_FORBIDDEN",
  "MACRO_ACTION_BLOCKED", "MACRO_ACTION_FORBIDDEN",
  "LUA_WARNING",
}) do
  tryRegister(event)
end

local function countThisSession()
  local n = 0
  for _, e in ipairs(db.errors) do
    if e.session == sessionId or e.lastSession == sessionId then
      n = n + 1
    end
  end
  return n
end

frame:SetScript("OnEvent", function(_, event, ...)
  if event == "ADDON_LOADED" then
    if ... == ADDON_NAME then
      initDatabase()
    end
  elseif event == "PLAYER_LOGIN" then
    local total, failed = DevBridge.RunProbes()
    local s = db.session
    chat(string.format(
      "session %d, client %s (build %s, interface %s). %d probes run, %d failed. %d error(s) this session. /devbridge for commands.",
      s.id, tostring(s.version), tostring(s.build), tostring(s.interface),
      total, failed, countThisSession()))
  else
    local args = {}
    for i = 1, select("#", ...) do
      args[#args + 1] = safeToString((select(i, ...)))
    end
    local kind = event == "LUA_WARNING" and "warning" or "blocked"
    recordError(kind, event .. ": " .. table.concat(args, ", "))
  end
end)

---------------------------------------------------------------------------
-- Slash commands
---------------------------------------------------------------------------

local function showErrors(limit)
  local list = db.errors
  if #list == 0 then
    chat("no errors recorded.")
    return
  end
  local first = math.max(1, #list - limit + 1)
  for i = first, #list do
    local e = list[i]
    chat(string.format("[%d] %s x%d: %s", i, e.kind, e.count or 1, e.message))
  end
end

SLASH_DEVBRIDGE1 = "/devbridge"
SLASH_DEVBRIDGE2 = "/dvb"
SlashCmdList.DEVBRIDGE = function(msg)
  local command, rest = (msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
  command = (command or ""):lower()
  if not db then
    chat("not ready yet (SavedVariables not loaded).")
  elseif command == "errors" then
    showErrors(5)
  elseif command == "clear" then
    db.errors = {}
    db.log = {}
    chat("errors and log cleared.")
  elseif command == "mark" then
    DevBridge.Log("mark", rest ~= "" and rest or "(no text)")
    chat("mark recorded: " .. rest)
  elseif command == "probe" then
    local total, failed = DevBridge.RunProbes()
    -- Kept apart from db.probes, which every login (including /reload)
    -- replaces with a fresh automatic run; this copy lasts until the next
    -- /dvb probe, so a mid-session result survives any number of reloads.
    db.manualProbes = { ranAt = db.probesRanAt, session = sessionId, probes = db.probes }
    chat(string.format("%d probes run, %d failed, kept as the manual run. /reload to save them to disk.", total, failed))
  elseif command == "api" then
    local g, n, f = DevBridge.TakeApiInventory()
    chat(string.format("inventory taken: %d global functions, %d C_ namespaces holding %d functions. /reload to save it to disk.", g, n, f))
  elseif command == "status" or command == "" then
    chat(string.format("session %d. %d error(s) stored, %d this session. %d log line(s).",
      sessionId, #db.errors, countThisSession(), #db.log))
    chat("commands: /dvb errors | clear | mark <text> | probe | api | status")
    chat("after any command, /reload writes the results to disk for the dev tools.")
  else
    chat("unknown command '" .. command .. "'. Try /dvb status.")
  end
end
