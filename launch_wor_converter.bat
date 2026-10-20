@echo off
setlocal DisableDelayedExpansion
rem Use QGIS's environment, never system Python or the repository virtualenv.
set "WOR_RUNNER=%~dp0tools\run_wor_converter.py"
set "WOR_PYTHON="
if not exist "%WOR_RUNNER%" goto missing_runner
if defined QGIS_ROOT goto explicit_root

rem Prefer an installed QGIS 3.x runtime over QGIS 4.x.
for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\QGIS 3*" 2^>nul') do call :try_root "%ProgramFiles%\%%D"
if defined WOR_PYTHON goto launch
if defined ProgramFiles(x86) for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles(x86)%\QGIS 3*" 2^>nul') do call :try_root "%ProgramFiles(x86)%\%%D"
if defined WOR_PYTHON goto launch
rem Fall back to other QGIS installations, then common OSGeo4W roots.
for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles%\QGIS*" 2^>nul') do call :try_root "%ProgramFiles%\%%D"
if defined WOR_PYTHON goto launch
if defined ProgramFiles(x86) for /f "delims=" %%D in ('dir /b /ad /o-n "%ProgramFiles(x86)%\QGIS*" 2^>nul') do call :try_root "%ProgramFiles(x86)%\%%D"
call :try_root "C:\OSGeo4W"
call :try_root "C:\OSGeo4W64"
if defined WOR_PYTHON goto launch
goto missing_runtime

:explicit_root
call :try_root "%QGIS_ROOT%"
if defined WOR_PYTHON goto launch
echo QGIS_ROOT is set but has no bin\python-qgis.bat or bin\python-qgis-ltr.bat.
echo Set QGIS_ROOT to the QGIS installation folder, not its bin folder.
goto failure

:try_root
if defined WOR_PYTHON exit /b 0
if exist "%~1\bin\python-qgis.bat" set "WOR_PYTHON=%~1\bin\python-qgis.bat"
if defined WOR_PYTHON exit /b 0
if exist "%~1\bin\python-qgis-ltr.bat" set "WOR_PYTHON=%~1\bin\python-qgis-ltr.bat"
exit /b 0

:launch
echo Starting standalone WOR converter using "%WOR_PYTHON%"
call "%WOR_PYTHON%" "%WOR_RUNNER%"
set "WOR_RESULT=%ERRORLEVEL%"
if "%WOR_RESULT%"=="0" exit /b 0
echo The converter exited with an error. Check the QGIS installation and repository files.
pause
exit /b %WOR_RESULT%

:missing_runner
echo The converter runner is missing. Keep this launcher in the repository root.
goto failure

:missing_runtime
echo No installed QGIS Python runtime was found.
echo Install QGIS 3.x with Python support, or set QGIS_ROOT to its installation folder.
echo Example: set "QGIS_ROOT=C:\Program Files\QGIS 3.44.2"
echo Then run this launcher again. System Python and pip do not supply QGIS.

:failure
pause
exit /b 1
