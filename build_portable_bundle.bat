@echo off
setlocal
cd /d "%~dp0"

set "BUNDLE_ROOT=%~dp0portable_bundle\iPodThemeStudio_Portable"
set "RUNTIME_SRC=C:\Users\wxh\.conda\envs\ipod_theme"
set "RUNTIME_DST=%BUNDLE_ROOT%\runtime\python"

echo Preparing portable bundle at:
echo   %BUNDLE_ROOT%

if exist "%BUNDLE_ROOT%" rmdir /s /q "%BUNDLE_ROOT%"

mkdir "%BUNDLE_ROOT%" >nul
mkdir "%BUNDLE_ROOT%\runtime" >nul

echo Copying bundled Python runtime...
robocopy "%RUNTIME_SRC%" "%RUNTIME_DST%" /MIR /XD "__pycache__" ".ipynb_checkpoints" >nul
if errorlevel 8 goto :copyfail

echo Copying app files...
for %%F in (
  theme_studio.py
  theme_studio_core.py
  studio_icon.png
  studio_icon.ico
  LICENSE
  README.md
  README.zh-CN.md
  run_theme_studio.bat
) do (
  copy /y "%%F" "%BUNDLE_ROOT%\%%F" >nul
)

robocopy "ipodhax" "%BUNDLE_ROOT%\ipodhax" /MIR /XD "__pycache__" >nul
if errorlevel 8 goto :copyfail
robocopy "iPod_1.2_36B10147" "%BUNDLE_ROOT%\iPod_1.2_36B10147" /MIR >nul
if errorlevel 8 goto :copyfail
robocopy "iPod_1.1.2_39A10023_2012" "%BUNDLE_ROOT%\iPod_1.1.2_39A10023_2012" /MIR >nul
if errorlevel 8 goto :copyfail
robocopy "iPod_1.1.2_39A10023_2015" "%BUNDLE_ROOT%\iPod_1.1.2_39A10023_2015" /MIR >nul
if errorlevel 8 goto :copyfail

echo Writing portable launcher...
(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo.
  echo set "RUNTIME=%%~dp0runtime\python"
  echo set "PATH=%%RUNTIME%%;%%RUNTIME%%\Library\bin;%%RUNTIME%%\Scripts;%%PATH%%"
  echo set "PYTHONHOME=%%RUNTIME%%"
  echo set "PYTHONNOUSERSITE=1"
  echo.
  echo "%%RUNTIME%%\python.exe" theme_studio.py
  echo endlocal
) > "%BUNDLE_ROOT%\launch_theme_studio_portable.bat"

echo Writing portable README...
(
  echo iPod Theme Studio Portable
  echo ==========================
  echo.
  echo This folder is a no-install portable bundle for the GUI workflow.
  echo.
  echo How to use:
  echo 1. Extract the full folder anywhere on your PC.
  echo 2. Double-click "launch_theme_studio_portable.bat".
  echo 3. Use the GUI to import official firmware or a community IPSW, browse artwork, replace assets, and repack.
  echo.
  echo Notes:
  echo - Python and required libraries are bundled in "runtime\python".
  echo - Rust, Cargo, and ARM GCC are not needed for the GUI workflow.
  echo - Downloading official firmware still requires an internet connection.
  echo - Repacked IPSW files are for use with iTunes/Finder restore workflows outside this tool.
) > "%BUNDLE_ROOT%\README_PORTABLE.txt"

echo Portable bundle is ready.
echo Launch with:
echo   %BUNDLE_ROOT%\launch_theme_studio_portable.bat
endlocal
exit /b 0

:copyfail
echo Failed to copy files into the portable bundle.
exit /b 1
