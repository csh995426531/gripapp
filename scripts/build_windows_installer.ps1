# Build standalone Grip.exe (onedir) + Inno Setup installer on Windows.
# Requires: Python venv with grip[menubar], PyInstaller; optional Inno Setup 6 (ISCC).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Pip = Join-Path $Root ".venv\Scripts\pip.exe"
if (-not (Test-Path $Py)) { throw "Missing .venv — create venv and pip install -e .[menubar]" }

$Version = & $Py -c "from grip import __version__; print(__version__)"
Write-Host "==> deps"
& $Pip install -q -e ".[menubar]" "pyinstaller>=6.0"

Write-Host "==> pyinstaller"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\pyinstaller, dist\Grip
& $Py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\Grip.spec

$Exe = Join-Path $Root "dist\Grip\Grip.exe"
if (-not (Test-Path $Exe)) { throw "dist\Grip\Grip.exe missing" }

$Iscc = ${(Get-Command iscc -ErrorAction SilentlyContinue).Source}
if (-not $Iscc) {
  $candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { $Iscc = $c; break } }
}

if ($Iscc) {
  Write-Host "==> Inno Setup ($Iscc)"
  & $Iscc "/DMyAppVersion=$Version" (Join-Path $Root "packaging\windows\Grip.iss")
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
  Write-Host "Installer: dist\Grip-$Version-windows-setup.exe"
} else {
  Write-Host "Inno Setup (ISCC) not found — onedir app is at dist\Grip\"
  Write-Host "Install Inno Setup 6, then re-run this script for Setup.exe"
}

Get-ChildItem dist\Grip*, dist\Grip\Grip.exe -ErrorAction SilentlyContinue | Format-Table Name, Length
