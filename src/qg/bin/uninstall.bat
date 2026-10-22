@echo off
rem ============================================================
rem uninstall.bat - remove the qg wrapper folder from the USER Path
rem
rem Safe: touches only HKCU\Environment (no admin rights needed).
rem Idempotent: re-running when the entry is absent is a no-op.
rem
rem Must sit either in the wrapper "bin" folder itself or in its
rem parent (src\qg); both placements are handled below.
rem ============================================================
setlocal
set "SCRIPTDIR=%~dp0"

rem Derive the bin folder (same rule as install.bat).
if /i "%SCRIPTDIR:~-5%"=="\bin\" (
    set "BINDIR=%SCRIPTDIR:~0,-1%"
) else (
    set "BINDIR=%SCRIPTDIR%bin"
)

echo Wrapper folder: %BINDIR%

rem ---- Remove from user Path (idempotent), registry API only ----
rem Reads/writes ONLY HKCU\Environment\Path (the user Path), keeping
rem REG_EXPAND_SZ, so there is no setx truncation risk.
rem The target folder is passed via the PS env var QG_BIN_DIR rather
rem than as a positional argument, so it works under both cmd.exe
rem and PowerShell callers (a trailing %BINDIR% arg breaks under PS).
set "QG_BIN_DIR=%BINDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:QG_BIN_DIR; function Norm($p) { $p.Trim().TrimEnd('\').ToLower() }; $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true); $cur = [string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); $target = Norm $bin; $present = (($cur -split ';') | Where-Object { $_ -ne '' } | ForEach-Object { Norm $_ }) -contains $target; if (-not $present) { Write-Host 'Entry not present in user Path - nothing to do (idempotent).' } else { $new = ($cur -split ';' | Where-Object { $_ -ne '' -and (Norm $_) -ne $target }) -join ';'; $k.SetValue('Path', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString); Write-Host 'Entry removed from user Path.' }; $k.Close(); Write-Host ''; Write-Host '---- AFTER (user Path) ----'; [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment').GetValue('Path','','DoNotExpandEnvironmentNames')"

echo.
echo NOTE: Current open shells still carry the old Path copy.
echo       New terminals will no longer resolve "qg".
endlocal
exit /b 0
