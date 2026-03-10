@echo off
cd /d "%~dp0"
python -m PyInstaller ^
  --noconfirm ^
  --onedir ^
  --windowed ^
  --name iPodThemeStudio ^
  --collect-submodules ipodhax ^
  --collect-submodules pyfatfs ^
  --add-data "iPod_1.2_36B10147;iPod_1.2_36B10147" ^
  --add-data "iPod_1.1.2_39A10023_2012;iPod_1.1.2_39A10023_2012" ^
  --add-data "iPod_1.1.2_39A10023_2015;iPod_1.1.2_39A10023_2015" ^
  theme_studio.py
