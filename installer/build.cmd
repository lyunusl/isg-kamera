@echo off
rem Builds the single-file IsgKamera exe (the target computer needs no Python and no installation).
rem   installer\build.cmd            Community package: dist\community\IsgKamera-community.exe
rem   installer\build.cmd plain      with plain models: dist\IsgKamera.exe
rem If Inno Setup 6 is installed, also the installer: dist\installer\IsgKamera-Kurulum-0.1.0.exe
setlocal
cd /d "%~dp0.."

set MODE=%~1
if "%MODE%"=="" set MODE=community
rem The spec still reads the old variable name as a fallback: clear it so it cannot override MODE.
set ISG_MODEL_PAKETI=

if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller==6.19.0 || goto :fail
)
if not exist engine\deps\onnxruntime\__init__.py (
  echo engine\deps is not ready: see the development-from-source section of the README.
  goto :fail
)
if /i "%MODE%"=="community" if not exist installer\community-models\community-paket.json (
  echo Community model package not found: installer\community-models
  goto :fail
)

rem Development checks in tests\ (skipped when a check script is missing).
for %%S in (check_studio_setup check_engine_deploy check_headless check_model_license) do (
  if exist tests\%%S.py (
    .venv\Scripts\python tests\%%S.py >nul 2>&1 || (echo %%S failed & goto :fail)
  )
)
if not exist installer\scene\isg-kamera.pmod (
  echo Ready-made ISG scene missing: installer\scene\isg-kamera.pmod
  echo Produce it once with the steps in the README: installer\build_scene.py, then Save As in Studio.
  goto :fail
)
.venv\Scripts\python installer\make_icon.py >nul || goto :fail

if /i "%MODE%"=="plain" (
  set ISG_MODEL_PACKAGE=
  .venv\Scripts\python -m PyInstaller installer\IsgKamera.spec --noconfirm --clean --distpath dist --workpath build\plain || goto :fail
  echo.
  echo Ready: %cd%\dist\IsgKamera.exe
) else (
  set ISG_MODEL_PACKAGE=%MODE%
  .venv\Scripts\python -m PyInstaller installer\IsgKamera.spec --noconfirm --clean --distpath dist\%MODE% --workpath build\%MODE% || goto :fail
  echo.
  echo Ready: %cd%\dist\%MODE%\IsgKamera-%MODE%.exe
)

set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ISCC%" (
  "%ISCC%" /Q /DMode=%MODE% installer\IsgKamera.iss || goto :fail
  echo Installer: %cd%\dist\installer\IsgKamera-Kurulum-0.1.0.exe
) else (
  echo Inno Setup 6 is not installed: no installer was built. To install it: winget install JRSoftware.InnoSetup
)
exit /b 0

:fail
echo Build failed.
exit /b 1
