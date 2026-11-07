# Fetch the Fotobuch release autobook is tested with, check its checksum and put fotobuch.exe
# with its license notes into $Dest (the app looks for <app folder>\fotobuch\fotobuch.exe).
# Fotobuch is free software under the GNU AGPL v3: shipping it means shipping where its source is.
param(
    [string]$Dest = "dist\autobook\fotobuch",
    [string]$Version = "v0.3.0"
)
$ErrorActionPreference = "Stop"
$base = "https://github.com/EddyXorb/fotobuch/releases/download/$Version"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "fotobuch-$Version"
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "fotobuch.zip"
$sum = Join-Path $tmp "fotobuch.zip.sha256"
Invoke-WebRequest "$base/fotobuch-windows-x86_64.zip" -OutFile $zip
Invoke-WebRequest "$base/fotobuch-windows-x86_64.zip.sha256" -OutFile $sum
$want = (Get-Content $sum -Raw).Trim().Split(" ")[0].ToLower()
$got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { throw "fotobuch checksum mismatch: $got, expected $want" }
Expand-Archive $zip -DestinationPath (Join-Path $tmp "x") -Force
$exe = Get-ChildItem (Join-Path $tmp "x") -Recurse -Filter fotobuch.exe | Select-Object -First 1
if (-not $exe) { throw "fotobuch.exe not found in the archive" }
New-Item -ItemType Directory -Force $Dest | Out-Null
Copy-Item $exe.FullName $Dest
Copy-Item (Join-Path $PSScriptRoot "..\..\LICENSE") (Join-Path $Dest "LICENSE-fotobuch.txt")  # the same AGPL v3 text
Set-Content -Encoding utf8 (Join-Path $Dest "SOURCE-fotobuch.txt") @"
fotobuch $Version (https://github.com/EddyXorb/fotobuch) is free software under the GNU Affero
General Public License v3 (see LICENSE-fotobuch.txt). This copy is the unchanged release build.
Source code of this version: https://github.com/EddyXorb/fotobuch/tree/$Version
Archive: https://github.com/EddyXorb/fotobuch/archive/refs/tags/$Version.zip
"@
Write-Host "fotobuch $Version -> $Dest"
