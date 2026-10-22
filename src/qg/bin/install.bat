@echo off
rem qg-path-setup.bat - run from an ADMINISTRATOR cmd.
rem   1. add the qg bin folder to the SYSTEM Path (idempotent)
rem   2. remove the duplicate from the USER Path (only after step 1 is confirmed)
rem   3. broadcast the environment change
rem The bin folder is derived from this script's location: the script may sit
rem IN bin or in its parent (src\qg). An optional argument overrides it.
setlocal
set "D=%~dp0"
set "BINDIR=%D%bin"
if /i "%D:~-5%"=="\bin\" set "BINDIR=%D:~0,-1%"
if not "%~1"=="" set "BINDIR=%~1"
set "SELF=%~f0"
net session >nul 2>&1
if errorlevel 1 (echo ERROR: run this from an Administrator cmd. & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:SELF); iex $s.Substring($s.IndexOf('###PS'+'###')+8)"
exit /b %errorlevel%
###PS###
$ErrorActionPreference = 'Stop'
$bin = $env:BINDIR
Write-Host "Wrapper folder: $bin"
if (-not (Test-Path (Join-Path $bin 'qg.bat'))) { Write-Host "ERROR: $bin\qg.bat not found. Is this script inside the qg folder?"; exit 1 }

function Norm($p) { $p.Trim().TrimEnd('\').ToLower() }
function Has($path, $dir) { ($path -split ';' | Where-Object { $_ -ne '' } | ForEach-Object { Norm $_ }) -contains (Norm $dir) }

# ---- 1. SYSTEM Path (HKLM) ----
$sk = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true)
$sys = [string]$sk.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
if (Has $sys $bin) {
    Write-Host 'System Path: already contains the entry.'
} else {
    $new = if ($sys.Trim() -eq '' -or $sys.TrimEnd().EndsWith(';')) { $sys + $bin } else { $sys + ';' + $bin }
    $sk.SetValue('Path', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Write-Host 'System Path: entry added.'
}
$sysAfter = [string]$sk.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
$sk.Close()
if (-not (Has $sysAfter $bin)) { Write-Host 'ABORT: system Path still lacks the entry; user Path left untouched.'; exit 1 }

# ---- 2. USER Path (HKCU): remove the duplicate ----
# Admin cmd runs as the same account here; if it is a different admin account, HKCU is that account's.
$uk = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$usr = [string]$uk.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
if (Has $usr $bin) {
    $kept = $usr -split ';' | Where-Object { $_ -ne '' -and (Norm $_) -ne (Norm $bin) }
    $uk.SetValue('Path', ($kept -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Write-Host 'User Path: duplicate entry removed.'
} else {
    Write-Host 'User Path: entry not present, nothing to remove.'
}
$uk.Close()

# ---- 3. broadcast ----
Add-Type -Namespace Win32 -Name Native -MemberDefinition '[DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr h, uint m, UIntPtr w, string l, uint f, uint t, out UIntPtr r);'
$r = [UIntPtr]::Zero
[void][Win32.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r)

Write-Host "`nDone. Open a NEW cmd; if 'qg' is still not found, sign out and back in (lock screen is not enough)."