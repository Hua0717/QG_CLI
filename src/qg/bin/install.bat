@echo off
rem ============================================================
rem install.bat - add the qg wrapper folder to the USER Path
rem
rem Safe: touches only HKCU\Environment (no admin rights needed).
rem Idempotent: re-running never creates duplicates.
rem
rem The wrapper scripts (qg.bat, qg1.bat..qg6.bat) live in the
rem "bin" folder. This script may sit either IN bin itself or
rem in its parent (src\qg); both are handled below.
rem ============================================================
setlocal
set "SCRIPTDIR=%~dp0"

rem Derive the bin folder: if this script is already inside a
rem folder named "bin", use it as-is; otherwise append "\bin"
rem (script sitting in src\qg -> src\qg\bin).
if /i "%SCRIPTDIR:~-5%"=="\bin\" (
    set "BINDIR=%SCRIPTDIR:~0,-1%"
) else (
    set "BINDIR=%SCRIPTDIR%bin"
)

echo Wrapper folder: %BINDIR%
if not exist "%BINDIR%\qg.bat" (
    echo ERROR: %BINDIR%\qg.bat not found. Install script misplaced?
    exit /b 1
)

echo.
echo ---- BEFORE (user Path) ----
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment').GetValue('Path','','DoNotExpandEnvironmentNames')"

rem ---- Add to user Path (idempotent) via the PowerShell registry API ----
rem Reads/writes ONLY HKCU\Environment\Path (the user Path, never the
rem combined machine+user Path) and writes it back as REG_EXPAND_SZ,
rem avoiding setx's 1024-character truncation limit entirely.
rem The target folder is passed via the PS env var QG_BIN_DIR rather
rem than as a positional argument, so it works under both cmd.exe
rem and PowerShell callers (a trailing %BINDIR% arg breaks under PS).
set "QG_BIN_DIR=%BINDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:QG_BIN_DIR; function Norm($p) { $p.Trim().TrimEnd('\').ToLower() }; $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true); $cur = [string]$k.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); $parts = $cur -split ';' | Where-Object { $_ -ne '' }; $present = ($parts | ForEach-Object { Norm $_ }) -contains (Norm $bin); if ($present) { Write-Host 'User Path already contains the entry - nothing to do (idempotent).' } else { if ($cur.TrimEnd().EndsWith(';') -or $cur.Trim() -eq '') { $new = $cur + $bin } else { $new = $cur + ';' + $bin }; $k.SetValue('Path', $new, [Microsoft.Win32.RegistryValueKind]::ExpandString); Write-Host 'Entry added to user Path.' }; $k.Close(); Write-Host ''; Write-Host '---- AFTER (user Path) ----'; [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment').GetValue('Path','','DoNotExpandEnvironmentNames')"

echo.
echo NOTE: Only NEW terminals/processes will see the updated Path.
echo       Current open shells keep their old copy - open a new
echo       window (or restart the shell) for "qg" to resolve.

echo.
echo ---- Verification ----
"%BINDIR%\qg.bat" --help >"%TEMP%\qg_install_verify.txt" 2>&1
if errorlevel 1 (
    echo FAIL: "%BINDIR%\qg.bat" --help returned errorlevel %errorlevel%.
    echo Output:
    type "%TEMP%\qg_install_verify.txt"
    del /q "%TEMP%\qg_install_verify.txt" 2>nul
    endlocal & exit /b 1
) else (
    echo SUCCESS: "%BINDIR%\qg.bat" --help ran without error. Output:
    type "%TEMP%\qg_install_verify.txt"
    del /q "%TEMP%\qg_install_verify.txt" 2>nul
)
endlocal
exit /b 0
