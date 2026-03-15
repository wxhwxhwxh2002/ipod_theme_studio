@echo off
setlocal
cd /d "%~dp0"

set "BUNDLE_ROOT=%~dp0portable_bundle\iPodThemeStudio_Portable"
set "RUNTIME_DST=%BUNDLE_ROOT%\runtime\python"
set "RUNTIME_SRC="

if defined IPOD_THEME_RUNTIME_SRC set "RUNTIME_SRC=%IPOD_THEME_RUNTIME_SRC%"
if not defined RUNTIME_SRC if defined CONDA_PREFIX set "RUNTIME_SRC=%CONDA_PREFIX%"
if not defined RUNTIME_SRC if defined VIRTUAL_ENV set "RUNTIME_SRC=%VIRTUAL_ENV%"

if not defined RUNTIME_SRC (
  echo Could not detect a Python runtime to bundle.
  echo.
  echo Activate your conda or venv environment first, or set:
  echo   IPOD_THEME_RUNTIME_SRC=full_path_to_python_runtime
  echo.
  echo Examples:
  echo   conda activate ipod_theme
  echo   build_portable_bundle.bat
  echo.
  exit /b 1
)

if not exist "%RUNTIME_SRC%\python.exe" (
  echo Detected runtime root is invalid:
  echo   %RUNTIME_SRC%
  echo.
  echo Expected to find python.exe under that directory.
  exit /b 1
)

echo Preparing portable bundle at:
echo   %BUNDLE_ROOT%
echo Using Python runtime from:
echo   %RUNTIME_SRC%

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
  echo set "APP_ROOT=%%~dp0"
  echo pushd "%%APP_ROOT%%"
  echo.
  echo set "RUNTIME=%%APP_ROOT%%runtime\python"
  echo set "SCRIPT_PATH=%%APP_ROOT%%theme_studio.py"
  echo set "PYTHONEXE=%%RUNTIME%%\python.exe"
  echo set "PYTHONPATH="
  echo set "PYTHONHOME="
  echo set "PYTHONEXECUTABLE="
  echo set "PYTHONNOUSERSITE=1"
  echo set "CONDA_PREFIX="
  echo set "CONDA_DEFAULT_ENV="
  echo set "VIRTUAL_ENV="
  echo set "PATH=%%RUNTIME%%;%%RUNTIME%%\Library\bin;%%RUNTIME%%\Scripts;%%PATH%%"
  echo.
  echo if not exist "%%PYTHONEXE%%" ^(
  echo   echo Portable runtime not found: %%PYTHONEXE%%
  echo   pause
  echo   popd
  echo   endlocal
  echo   exit /b 1
  echo ^)
  echo.
  echo "%%PYTHONEXE%%" "%%SCRIPT_PATH%%"
  echo if errorlevel 1 ^(
  echo   echo.
  echo   echo Failed to launch iPod Theme Studio.
  echo   echo App root: %%APP_ROOT%%
  echo   echo Runtime: %%PYTHONEXE%%
  echo   pause
  echo   popd
  echo   endlocal
  echo   exit /b 1
  echo ^)
  echo popd
  echo endlocal
) > "%BUNDLE_ROOT%\launch_theme_studio_portable.bat"

echo Copying portable README...
copy /y "portable_templates\README_PORTABLE.txt" "%BUNDLE_ROOT%\README_PORTABLE.txt" >nul
if errorlevel 1 goto :copyfail

echo Copying Chinese portable docs...
powershell -NoProfile -ExecutionPolicy Bypass -File "portable_templates\copy_portable_docs.ps1" "%BUNDLE_ROOT%"
if errorlevel 1 goto :copyfail

echo Portable bundle is ready.
echo Launch with:
echo   %BUNDLE_ROOT%\launch_theme_studio_portable.bat
endlocal
exit /b 0

:copyfail
echo Failed to copy files into the portable bundle.
exit /b 1
