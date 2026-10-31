@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-Content -LiteralPath '%~f0' -Encoding UTF8 | Select-Object -Skip 4 | Out-String; Invoke-Expression $s"
pause
exit /b

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression

Write-Host "=== Balatro Telemetry Patcher (Shadow PC -> Linux Live Mode) ===" -ForegroundColor Cyan

# 1. Find Balatro.exe
$candidates = @(
    (Join-Path (Get-Location).Path "Balatro.exe"),
    "C:\Program Files (x86)\Steam\steamapps\common\Balatro\Balatro.exe",
    "C:\Program Files\Steam\steamapps\common\Balatro\Balatro.exe"
)
foreach ($drv in [System.IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady }) {
    $candidates += Join-Path $drv.RootDirectory.FullName "SteamLibrary\steamapps\common\Balatro\Balatro.exe"
    $candidates += Join-Path $drv.RootDirectory.FullName "Steam\steamapps\common\Balatro\Balatro.exe"
}
$exePath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $exePath) {
    Add-Type -AssemblyName System.Windows.Forms
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Title = "Please select Balatro.exe"
    $dlg.Filter = "Balatro.exe|Balatro.exe|All files|*.*"
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $exePath = $dlg.FileName
    } else {
        throw "Balatro.exe was not found."
    }
}

Write-Host "Found: $exePath" -ForegroundColor Green

# Close a running game so the file is not locked
$proc = Get-Process -Name "Balatro" -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Closing the running Balatro..." -ForegroundColor Yellow
    $proc | Stop-Process -Force
    Start-Sleep -Seconds 1
}

$bakPath = "$exePath.bak"
if (-not (Test-Path -LiteralPath $bakPath)) {
    Write-Host "Creating backup: $bakPath" -ForegroundColor Cyan
    Copy-Item -LiteralPath $exePath -Destination $bakPath
} else {
    Write-Host "Using the clean original backup: $bakPath" -ForegroundColor Cyan
}

# 2. Split the PE header and the appended LÖVE2D ZIP exactly at the EOCD
[byte[]]$raw = [System.IO.File]::ReadAllBytes($bakPath)
$eocd = -1
$minPos = [Math]::Max(0, $raw.Length - 65557)
for ($i = $raw.Length - 22; $i -ge $minPos; $i--) {
    if ($raw[$i] -eq 0x50 -and $raw[$i+1] -eq 0x4B -and $raw[$i+2] -eq 0x05 -and $raw[$i+3] -eq 0x06) {
        $eocd = $i
        break
    }
}
if ($eocd -lt 0) { throw "No ZIP archive found in Balatro.exe." }

$cdSize   = [BitConverter]::ToUInt32($raw, $eocd + 12)
$cdOffset = [BitConverter]::ToUInt32($raw, $eocd + 16)
$zipStart = [int]($eocd - $cdSize - $cdOffset)

if ($raw[$zipStart] -ne 0x50 -or $raw[$zipStart+1] -ne 0x4B) {
    throw "Ungueltiger ZIP-Startoffset ($zipStart)."
}
Write-Host ("EXE-Header: {0} Bytes | Spiel-Archiv: {1} Bytes" -f $zipStart, ($raw.Length - $zipStart))

# 3. Lua telemetry hook (after G:draw() it draws the compressed game state as a strip of 4x4 blocks)
$luaHook = @'

-- BALATRO_TELEMETRY_V2
do
  local _orig_draw = love.draw
  local _bit = require("bit")
  local _pkt, _last_t = nil, -1

  local function _clean(s)
    return tostring(s or ""):gsub("[\t\r\n]", " ")
  end

  local function _pos(c, W, H)
    local T = c and (c.VT or c.T)
    local P = c and c.container and c.container.T
    if not T or not P or W == 0 or H == 0 then return 0, 0 end
    local s = (G.TILESCALE or 1) * (G.TILESIZE or 20)
    return (T.x + T.w * 0.5 + P.x) * s / W, (T.y + T.h * 0.5 + P.y) * s / H
  end

  local function _ed(c)
    local t = c and c.edition and c.edition.type or ""
    return t == "polychrome" and "poly" or t
  end

  local function _enh(c)
    if not c or not c.config or not c.config.center or c.config.center.set ~= "Enhanced" then return "" end
    return string.lower(c.config.center.name or ""):gsub(" card$", "")
  end

  local function _rank(c)
    local v = c and c.base and c.base.value or "2"
    local m = {Jack="J", Queen="Q", King="K", Ace="A"}
    return m[v] or v
  end

  local function _suit(c)
    local s = c and c.base and c.base.suit or "Spades"
    return string.sub(s, 1, 1)
  end

  local function _tok(c)  -- deck card: "KS" or "KS/<enhancement>/<edition>/<seal>"
    local t = _rank(c) .. _suit(c)
    local e, d, s = _enh(c), _ed(c), string.lower(c.seal or "")
    if e ~= "" or d ~= "" or s ~= "" then t = t .. "/" .. e .. "/" .. d .. "/" .. s end
    return t
  end

  local function _deck(tag, cards)
    local t = {}
    for _, c in ipairs(cards or {}) do t[#t+1] = _tok(c) end
    return tag .. "\t" .. table.concat(t, " ")
  end

  local function _cname(tbl, key)
    return key and tbl and tbl[key] and _clean(tbl[key].name) or ""
  end

  local function _cards_sorted(area)
    local out = {}
    for _, c in ipairs(area and area.cards or {}) do out[#out+1] = c end
    table.sort(out, function(a, b)
      local ax = (a.VT or a.T) and (a.VT or a.T).x or 0
      local bx = (b.VT or b.T) and (b.VT or b.T).x or 0
      return ax < bx
    end)
    return out
  end

  local function _build_state()
    if not G or not G.GAME or G.STAGE ~= G.STAGES.RUN then return "B\tother\t\t0\t0\t0\t0\t8\t0\t1\t1\t5\t5\t\t0\t0\t0" end
    local W, H = love.graphics.getWidth(), love.graphics.getHeight()
    local st = G.STATE
    local phase = "other"
    if st == G.STATES.SELECTING_HAND then phase = "playing"
    elseif st == G.STATES.SHOP and G.shop_jokers then phase = "shop"
    elseif st == G.STATES.BLIND_SELECT then phase = "blind_select"
    elseif st == G.STATES.ROUND_EVAL and G.round_eval then phase = "cash_out"
    elseif (st == G.STATES.TAROT_PACK or st == G.STATES.PLANET_PACK or st == G.STATES.SPECTRAL_PACK
            or st == G.STATES.STANDARD_PACK or st == G.STATES.BUFFOON_PACK)
           and G.pack_cards and G.pack_cards.cards and #G.pack_cards.cards > 0 then
      phase = "booster_pack"
    elseif st == G.STATES.GAME_OVER then phase = "game_over"
    end

    local gm = G.GAME
    local cr = gm.current_round or {}
    local bl = gm.blind or {}
    local bcol = gm.blind_on_deck == "Small" and 0 or (gm.blind_on_deck == "Big" and 1 or 2)
    local t_used = (gm.consumeable_usage_total and gm.consumeable_usage_total.tarot) or 0
    local lines = {string.format("B\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\t%d\t%d",
      phase, _clean(bl.name), math.floor(bl.chips or 0), math.floor(gm.chips or 0),
      cr.hands_left or 0, cr.discards_left or 0,
      (G.hand and G.hand.config and G.hand.config.card_limit) or 8,
      math.floor(gm.dollars or 0), (gm.round_resets and gm.round_resets.ante) or 1,
      gm.round or 1, cr.reroll_cost or 5,
      (G.jokers and G.jokers.config and G.jokers.config.card_limit) or 5,
      _clean(gm.selected_back and gm.selected_back.name),
      bl.disabled and 1 or 0, bcol, t_used)}

    for _, c in ipairs(_cards_sorted(G.hand)) do
      local fx, fy = _pos(c, W, H)
      local ab = c.ability or {}
      lines[#lines+1] = string.format("H\t%s\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%d\t%.4f\t%.4f",
        _rank(c), _suit(c), _enh(c), _ed(c), string.lower(c.seal or ""),
        c.debuff and 1 or 0, (c.facing == "back") and 1 or 0,
        ab.forced_selection and 1 or 0, math.floor(ab.perma_bonus or 0),
        c.highlighted and 1 or 0, fx, fy)
    end

    for _, c in ipairs(_cards_sorted(G.jokers)) do
      local fx, fy = _pos(c, W, H)
      local ab = c.ability or {}
      local ex = type(ab.extra) == "table" and ab.extra or {}
      local jname = _clean(ab.name)
      local jsuit, jrank = "", ""
      if jname == "Ancient Joker" and cr.ancient_card then jsuit = string.sub(cr.ancient_card.suit or "S", 1, 1)
      elseif jname == "Castle" and cr.castle_card then jsuit = string.sub(cr.castle_card.suit or "S", 1, 1)
      elseif jname == "The Idol" and cr.idol_card then
        jsuit = string.sub(cr.idol_card.suit or "S", 1, 1)
        jrank = tostring(cr.idol_card.id or 14)
      elseif jname == "Mail-In Rebate" and cr.mail_card then
        jrank = tostring(cr.mail_card.id or 14)
      end
      lines[#lines+1] = string.format("J\t%s\t%s\t%d\t%d\t%d\t%d\t%.4f\t%.4f\t%s\t%s\t%s\t%d\t%s\t%s\t%s\t0\t%d\t%d\t%d",
        jname, _ed(c), c.debuff and 1 or 0, math.floor(c.sell_cost or 1),
        ab.eternal and 1 or 0, c.highlighted and 1 or 0, fx, fy,
        tostring(ab.mult or 0), tostring(ex.chips or 0),
        tostring(ab.caino_xmult or ab.x_mult or 1),
        ab.loyalty_remaining or -1, _clean(ab.to_do_poker_hand), jsuit, jrank,
        ab.rental and 1 or 0, ab.perishable and (ab.perish_tally or 0) or -1, ab.yorick_discards or -1)
    end

    for _, c in ipairs(_cards_sorted(G.consumeables)) do
      local fx, fy = _pos(c, W, H)
      local ab = c.ability or {}
      lines[#lines+1] = string.format("C\t%s\t%s\t%.4f\t%.4f",
        _clean(ab.name), string.lower(ab.set or "tarot"), fx, fy)
    end

    if phase == "shop" then
      local kmap = {Joker="joker", Planet="planet", Tarot="tarot", Spectral="spectral",
                    Voucher="voucher", Booster="booster_pack", Default="playing_card", Enhanced="playing_card"}
      for _, area in ipairs({G.shop_jokers, G.shop_vouchers, G.shop_booster}) do
        for _, c in ipairs(_cards_sorted(area)) do
          local fx, fy = _pos(c, W, H)
          local ab = c.ability or {}
          local kind = kmap[ab.set or ""] or "joker"
          local cname = kind == "playing_card" and (_rank(c) .. _suit(c)) or _clean(ab.name)
          lines[#lines+1] = string.format("S\t%s\t%s\t%d\t%s\t%.4f\t%.4f\t%d\t%d",
            kind, cname, math.floor(c.cost or 0), _ed(c), fx, fy,
            ab.rental and 1 or 0, ab.perishable and 1 or 0)
        end
      end
    elseif phase == "booster_pack" then
      local pmap = {[G.STATES.TAROT_PACK]="Arcana", [G.STATES.PLANET_PACK]="Celestial",
                    [G.STATES.SPECTRAL_PACK]="Spectral", [G.STATES.STANDARD_PACK]="Standard",
                    [G.STATES.BUFFOON_PACK]="Buffoon"}
      local ptype = pmap[st] or "Buffoon"
      local ch = gm.pack_choices or 1
      local sz = gm.pack_size or 3
      local pref = ch >= 2 and "Mega " or (sz >= 4 and "Jumbo " or "")
      lines[#lines+1] = string.format("P\t%s%s Pack\t%d", pref, ptype, ch)
      local kmap = {Joker="joker", Planet="planet", Tarot="tarot", Spectral="spectral",
                    Default="playing_card", Enhanced="playing_card"}
      for _, c in ipairs(_cards_sorted(G.pack_cards)) do
        local fx, fy = _pos(c, W, H)
        local ab = c.ability or {}
        local kind = kmap[ab.set or ""] or "joker"
        if kind == "playing_card" then
          lines[#lines+1] = string.format("K\tplaying_card\t%s%s\t%s\t%s\t%s\t%.4f\t%.4f",
            _rank(c), _suit(c), _ed(c), _enh(c), string.lower(c.seal or ""), fx, fy)
        else
          lines[#lines+1] = string.format("K\t%s\t%s\t%s\t\t\t%.4f\t%.4f",
            kind, _clean(ab.name), _ed(c), fx, fy)
        end
      end
    end

    for hname, hinfo in pairs(gm.hands or {}) do
      local lv, pl, pr = hinfo.level or 1, hinfo.played or 0, hinfo.played_this_round or 0
      if lv > 1 or pl > 0 or pr > 0 then
        lines[#lines+1] = string.format("L\t%s\t%d\t%d\t%d", hname, lv, pl, pr)
      end
    end

    local vs = {}
    for k, v in pairs(gm.used_vouchers or {}) do
      if v and G.P_CENTERS and G.P_CENTERS[k] and G.P_CENTERS[k].name then
        vs[#vs+1] = _clean(G.P_CENTERS[k].name)
      end
    end
    if #vs > 0 then lines[#lines+1] = "V\t" .. table.concat(vs, "\t") end

    -- X: last tarot/planet (The Fool), upcoming boss, skip tags of Small/Big
    local rr = gm.round_resets or {}
    local bc, bt = rr.blind_choices or {}, rr.blind_tags or {}
    lines[#lines+1] = string.format("X\t%s\t%s\t%s\t%s",
      _cname(G.P_CENTERS, gm.last_tarot_planet), _cname(G.P_BLINDS, bc.Boss),
      _cname(G.P_TAGS, bt.Small), _cname(G.P_TAGS, bt.Big))
    lines[#lines+1] = _deck("F", G.playing_cards)          -- whole deck
    if phase == "playing" then lines[#lines+1] = _deck("D", G.deck and G.deck.cards) end  -- draw pile

    return table.concat(lines, "\n")
  end

  function love.draw()
    _orig_draw()
    pcall(function()
      local now = love.timer.getTime()
      if not _pkt or now - _last_t >= 0.08 then
        _last_t = now
        local z = love.data.compress("string", "zlib", _build_state())
        _pkt = "BL" .. string.char(math.floor(#z / 256), #z % 256) .. z
      end
      local W, H = love.graphics.getWidth(), love.graphics.getHeight()
      local cols = 320
      local bw, bh = W / cols, H / 240
      local nbits = #_pkt * 8
      local rows = math.ceil(nbits / cols)
      love.graphics.push("all")
      love.graphics.origin()
      love.graphics.setCanvas()
      love.graphics.setShader()
      love.graphics.setColor(45 / 255, 45 / 255, 45 / 255, 1)
      love.graphics.rectangle("fill", 0, bh, W, rows * bh)
      love.graphics.setColor(245 / 255, 245 / 255, 245 / 255, 1)
      for i = 0, nbits - 1 do
        local b = string.byte(_pkt, math.floor(i / 8) + 1)
        if _bit.band(_bit.rshift(b, 7 - (i % 8)), 1) == 1 then
          local c = i % cols
          local r = math.floor(i / cols) + 1
          love.graphics.rectangle("fill", c * bw, r * bh, bw, bh)
        end
      end
      love.graphics.pop()
    end)
  end
end
'@

# 4. Update main.lua inside the ZIP archive
$zipLen = $raw.Length - $zipStart
$ms = New-Object System.IO.MemoryStream
$ms.Write($raw, $zipStart, $zipLen)
$ms.Position = 0

$archive = New-Object System.IO.Compression.ZipArchive($ms, [System.IO.Compression.ZipArchiveMode]::Update, $true)
$entry = $archive.GetEntry("main.lua")
if (-not $entry) { throw "main.lua not found in Balatro.exe." }

$reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
$mainLua = $reader.ReadToEnd()
$reader.Close()

$entry.Delete()
$newEntry = $archive.CreateEntry("main.lua", [System.IO.Compression.CompressionLevel]::Optimal)
$writer = New-Object System.IO.StreamWriter($newEntry.Open(), (New-Object System.Text.UTF8Encoding($false)))
$writer.Write($mainLua + "`n" + $luaHook)
$writer.Close()
$archive.Dispose()

# 5. Write the patched Balatro.exe (EXE header + new ZIP)
$newZip = $ms.ToArray()
$ms.Close()

$outStream = [System.IO.File]::Create($exePath)
$outStream.Write($raw, 0, $zipStart)
$outStream.Write($newZip, 0, $newZip.Length)
$outStream.Close()

Write-Host ""
Write-Host "PATCHED SUCCESSFULLY! You can start Balatro now." -ForegroundColor Green
Write-Host "(To uninstall, rename '$bakPath' back to 'Balatro.exe'.)" -ForegroundColor Gray
