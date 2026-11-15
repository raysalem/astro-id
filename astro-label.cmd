@echo off
rem astro-label.cmd - label night-sky photos with astro-id (Windows).
rem   Drag one or more photos (or a folder) onto this file, or double-click and type a path.
rem   Writes <name>_astro.jpg (a labeled COPY) next to each photo; the original is never touched.
rem   Options work from a command line:  astro-label.cmd "D:\photos" --out both --to "D:\labeled"
setlocal
cd /d "%~dp0"
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" --version >nul 2>nul || (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again.
  pause
  exit /b 2
)
set "SECOND=%~2"
if "%~1"=="" goto ask
if "%SECOND%"=="" goto one
if "%SECOND:~0,1%"=="-" goto one
rem several dropped photos: label each one in turn
for %%A in (%*) do "%UV%" run --quiet astro-id label "%%~A"
goto done
:ask
set /p "TARGET=Photo or folder to label (paste a path): "
"%UV%" run --quiet astro-id label "%TARGET:"=%"
goto done
:one
"%UV%" run --quiet astro-id label %*
:done
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
