@echo off
rem Builds ParadoxModManager.exe locally from paradox_mod_manager.py (same folder as this file).
rem Releases on GitHub are built automatically; this is only for building it yourself.
setlocal
cd /d "%~dp0"
set PYTHONNOUSERSITE=1

set PY=python
where python >nul 2>nul || set PY=py

echo Installing / updating PyInstaller...
%PY% -s -m pip install --upgrade "pyinstaller>=6,<7" || goto :fail

echo Building ParadoxModManager.exe...
%PY% -s -m PyInstaller --noconfirm --clean --onefile --windowed --name ParadoxModManager paradox_mod_manager.py || goto :fail

copy /y "dist\ParadoxModManager.exe" "ParadoxModManager.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q ParadoxModManager.spec 2>nul

echo.
echo Done: %~dp0ParadoxModManager.exe
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
