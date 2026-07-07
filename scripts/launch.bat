@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%docker-compose.images.yml" (
  set "ROOT_DIR=%SCRIPT_DIR%"
) else if exist "%SCRIPT_DIR%docker-compose.yml" (
  set "ROOT_DIR=%SCRIPT_DIR%"
) else (
  set "ROOT_DIR=%SCRIPT_DIR%.."
)
pushd "%ROOT_DIR%" >nul || (
  echo Could not change to repository directory.>&2
  exit /b 1
)

if not defined PORTAL_HTTP_PORT set "PORTAL_HTTP_PORT=8080"
set "URL=http://localhost:%PORTAL_HTTP_PORT%"

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker is not installed. Install Docker Desktop first.>&2
  echo See https://docs.docker.com/get-docker/>&2
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is installed but not running. Start Docker Desktop, then try again.>&2
  exit /b 1
)

if not defined PODOCRACY_COMPOSE_IMAGES_FILE set "PODOCRACY_COMPOSE_IMAGES_FILE=docker-compose.images.yml"
if not defined PODOCRACY_COMPOSE_SOURCE_FILE set "PODOCRACY_COMPOSE_SOURCE_FILE=docker-compose.yml"
if not defined PODOCRACY_LAUNCH_MODE set "PODOCRACY_LAUNCH_MODE=auto"

rem Keep mode/file selection in sync with scripts/_launch-common.sh.
if /I "%PODOCRACY_LAUNCH_MODE%"=="images" (
  set "COMPOSE_FILE=%PODOCRACY_COMPOSE_IMAGES_FILE%"
) else if /I "%PODOCRACY_LAUNCH_MODE%"=="source" (
  set "COMPOSE_FILE=%PODOCRACY_COMPOSE_SOURCE_FILE%"
) else if /I "%PODOCRACY_LAUNCH_MODE%"=="auto" (
  if exist "%PODOCRACY_COMPOSE_IMAGES_FILE%" (
    set "COMPOSE_FILE=%PODOCRACY_COMPOSE_IMAGES_FILE%"
  ) else (
    set "COMPOSE_FILE=%PODOCRACY_COMPOSE_SOURCE_FILE%"
  )
) else (
  echo Unknown PODOCRACY_LAUNCH_MODE: %PODOCRACY_LAUNCH_MODE% ^(use auto, images, or source^).>&2
  popd >nul
  exit /b 1
)

if not exist "%COMPOSE_FILE%" (
  echo Compose file not found: %COMPOSE_FILE%>&2
  exit /b 1
)

if /I "%PODOCRACY_PULL_IMAGES%"=="1" (
  echo Pulling images from %COMPOSE_FILE%...
  docker compose -f "%COMPOSE_FILE%" pull
  if errorlevel 1 docker-compose -f "%COMPOSE_FILE%" pull
)

echo Starting Podocracy Worker Portal from %COMPOSE_FILE%...
docker compose -f "%COMPOSE_FILE%" up -d
if errorlevel 1 (
  docker-compose -f "%COMPOSE_FILE%" up -d
  if errorlevel 1 exit /b 1
)

set "TIMEOUT=%PODOCRACY_LAUNCH_TIMEOUT%"
if not defined TIMEOUT set "TIMEOUT=90"

echo Waiting for %URL%/api/health
set /a ATTEMPT=1
:wait_loop
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%URL%/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto ready

if %ATTEMPT% GEQ %TIMEOUT% (
  echo.
  echo Portal did not become ready in time. Check logs with: docker compose logs>&2
  popd >nul
  exit /b 1
)

<nul set /p=.
set /a ATTEMPT+=1
timeout /t 1 /nobreak >nul
goto wait_loop

:ready
echo.
echo Portal is ready: %URL%

if /I "%PODOCRACY_NO_BROWSER%"=="1" (
  echo Open this URL in your browser: %URL%
  popd >nul
  exit /b 0
)

start "" "%URL%"
popd >nul
exit /b 0
