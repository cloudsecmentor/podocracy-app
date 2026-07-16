<#
.SYNOPSIS
  Podocracy desktop launcher for Windows (no terminal required).

.DESCRIPTION
  The Windows counterpart to Podocracy.app. It:
    1. makes sure Docker Desktop is installed and running,
    2. picks (or reuses) an app-home folder - including adopting an existing
       command-line setup - and downloads the compose file when needed,
    3. asks for the OpenAI API key on first run and writes .env for the user,
    4. starts the containers, waits for /api/health, opens the browser, and exits.

  Run it invisibly from a shortcut so it feels like an app:
    powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File podocracy-windows-run.ps1
#>

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

# Explicit overrides (power users / tests). $null/empty means "not set by the user".
$HomeEnv     = $env:PODOCRACY_HOME
$ProjectsEnv = $env:PODOCRACY_PROJECTS_DIR
$PortEnv     = $env:PORTAL_HTTP_PORT

$RawBase            = if ($env:PODOCRACY_RAW_BASE) { $env:PODOCRACY_RAW_BASE } else { 'https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main' }
$ImagesComposeFile  = if ($env:PODOCRACY_COMPOSE_IMAGES_FILE) { $env:PODOCRACY_COMPOSE_IMAGES_FILE } else { 'docker-compose.images.yml' }
$SourceComposeFile  = if ($env:PODOCRACY_COMPOSE_SOURCE_FILE) { $env:PODOCRACY_COMPOSE_SOURCE_FILE } else { 'docker-compose.yml' }
$ComposeFile        = $ImagesComposeFile
$Port               = if ($PortEnv) { $PortEnv } else { '8080' }
$Url                = "http://localhost:$Port"
$DockerUrl          = 'https://www.docker.com/products/docker-desktop/'
$OpenAiKeysUrl      = 'https://platform.openai.com/api-keys'
$DefaultHome        = Join-Path $env:USERPROFILE 'Podocracy'
$ConfigDir          = if ($env:PODOCRACY_CONFIG_DIR) { $env:PODOCRACY_CONFIG_DIR } else { Join-Path $env:APPDATA 'Podocracy' }
$ConfigFile         = Join-Path $ConfigDir 'home.path'

function Show-Info($title, $message) {
  [System.Windows.Forms.MessageBox]::Show($message, $title, 'OK', 'Information') | Out-Null
}
function Show-Alert($title, $message) {
  [System.Windows.Forms.MessageBox]::Show($message, $title, 'OK', 'Warning') | Out-Null
}
function Confirm-Action($title, $message) {
  return ([System.Windows.Forms.MessageBox]::Show($message, $title, 'YesNo', 'Question') -eq 'Yes')
}

function Prompt-Secret($title, $message) {
  $form = New-Object System.Windows.Forms.Form
  $form.Text = $title
  $form.Size = New-Object System.Drawing.Size(470, 200)
  $form.StartPosition = 'CenterScreen'
  $form.FormBorderStyle = 'FixedDialog'
  $form.TopMost = $true

  $label = New-Object System.Windows.Forms.Label
  $label.Text = $message
  $label.SetBounds(14, 14, 430, 48)

  $box = New-Object System.Windows.Forms.TextBox
  $box.UseSystemPasswordChar = $true
  $box.SetBounds(14, 68, 430, 24)

  $ok = New-Object System.Windows.Forms.Button
  $ok.Text = 'Save'
  $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
  $ok.SetBounds(270, 110, 80, 30)

  $cancel = New-Object System.Windows.Forms.Button
  $cancel.Text = 'Cancel'
  $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
  $cancel.SetBounds(360, 110, 84, 30)

  $form.AcceptButton = $ok
  $form.CancelButton = $cancel
  $form.Controls.AddRange(@($label, $box, $ok, $cancel))

  if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $box.Text }
  return $null
}

function Select-FolderDialog($description) {
  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
  $dlg.Description = $description
  $dlg.ShowNewFolderButton = $true
  if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.SelectedPath }
  return $null
}

# Read KEY=... from a .env file (last match wins); returns $null when absent.
function Get-EnvValue($file, $key) {
  if (-not (Test-Path $file)) { return $null }
  $match = Select-String -Path $file -Pattern "^\s*$key=" -ErrorAction SilentlyContinue | Select-Object -Last 1
  if (-not $match) { return $null }
  return ($match.Line -replace "^\s*$key=", '').Trim().Trim('"').Trim("'")
}

function Test-PodocracyDir($dir) {
  return ((Test-Path (Join-Path $dir '.env')) -or
          (Test-Path (Join-Path $dir $ImagesComposeFile)) -or
          (Test-Path (Join-Path $dir $SourceComposeFile)))
}

function Find-ExistingHome {
  foreach ($c in @(
      (Join-Path $env:USERPROFILE 'podocracy-worker-portal'),
      (Join-Path $env:USERPROFILE 'Podocracy'),
      (Join-Path $env:USERPROFILE 'podocracy'))) {
    if (Test-PodocracyDir $c) { return $c }
  }
  return $null
}

function Resolve-Home {
  if ($HomeEnv) { return $HomeEnv }
  if (Test-Path $ConfigFile) {
    $saved = (Get-Content $ConfigFile -TotalCount 1 -ErrorAction SilentlyContinue)
    if ($saved -and (Test-Path $saved)) { return $saved }
  }

  $found = Find-ExistingHome
  $chosen = $null
  if ($found) {
    if (Confirm-Action 'Set up Podocracy' "Found an existing Podocracy setup at:`n$found`n`nUse this folder?") {
      $chosen = $found
    }
    elseif (Confirm-Action 'Set up Podocracy' "Choose a different existing folder yourself?`n`n(No = create a new folder at $DefaultHome)") {
      $chosen = Select-FolderDialog 'Select your existing Podocracy folder'
    }
  }
  else {
    if (Confirm-Action 'Set up Podocracy' "Do you already have a Podocracy folder (for example from the command-line setup)?`n`n(Yes = choose it, No = create a new one at $DefaultHome)") {
      $chosen = Select-FolderDialog 'Select your existing Podocracy folder'
    }
  }

  if (-not $chosen) { $chosen = $DefaultHome }
  New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
  Set-Content -Path $ConfigFile -Value $chosen -Encoding ascii
  return $chosen
}

function Test-DockerRunning {
  try { & docker info *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

function Ensure-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    if ($hasWinget -and (Confirm-Action 'Docker is required' "Podocracy runs on Docker Desktop, which isn't installed yet. Install it now with winget? A window will show progress; this can take several minutes.")) {
      Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile', '-Command', 'winget install -e --id Docker.DockerDesktop' -Wait
      Show-Info 'Finish Docker setup' 'When installation finishes, open Docker Desktop once (accept its prompts), then open Podocracy again.'
    }
    else {
      Show-Alert 'Docker is required' "Podocracy needs Docker Desktop. We'll open the download page now. Install Docker, start it once, then open Podocracy again."
      Start-Process $DockerUrl
    }
    exit 0
  }

  if (-not (Test-DockerRunning)) {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktop) { Start-Process $desktop } else { Start-Process $DockerUrl }

    $timeout = if ($env:PODOCRACY_DOCKER_TIMEOUT) { [int]$env:PODOCRACY_DOCKER_TIMEOUT } else { 120 }
    $waited = 0
    while (-not (Test-DockerRunning)) {
      Start-Sleep -Seconds 2
      $waited += 2
      if ($waited -ge $timeout) {
        Show-Alert 'Docker did not start' 'Docker Desktop did not finish starting in time. Open it, wait for the whale icon to settle, then open Podocracy again.'
        exit 1
      }
    }
  }
}

function Ensure-Home($home) {
  New-Item -ItemType Directory -Force -Path (Join-Path $home 'projects') | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $home 'logs') | Out-Null

  $imagesPath = Join-Path $home $ImagesComposeFile
  $sourcePath = Join-Path $home $SourceComposeFile
  if (-not (Test-Path $imagesPath) -and -not (Test-Path $sourcePath)) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$RawBase/$ImagesComposeFile" -OutFile $imagesPath
    }
    catch {
      Show-Alert 'Download failed' "Couldn't download the Podocracy configuration. Check your internet connection and open Podocracy again."
      exit 1
    }
  }

  if (Test-Path $imagesPath) { $script:ComposeFile = $ImagesComposeFile } else { $script:ComposeFile = $SourceComposeFile }
}

function Write-EnvFile($home, $key) {
  $content = @"
# Written by the Podocracy first-run setup. Edit here to change keys later.
OPENAI_API_KEY=$key
DEEPL_AUTH_KEY=
ELEVENLABS_API_KEY=

PORTAL_HTTP_PORT=$Port
PORTAL_ADMIN_PASSWORD=
WORKER_POLL_SECONDS=3

OPENAI_TRANSCRIBE_MODEL=whisper-1
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=alloy
"@
  Set-Content -Path (Join-Path $home '.env') -Value $content -Encoding ascii
}

function Ensure-Env($home) {
  if (Test-Path (Join-Path $home '.env')) { return }

  Show-Info 'Welcome to Podocracy' "First, let's add your OpenAI API key so Podocracy can transcribe and voice your projects. You can get a key at $OpenAiKeysUrl. The next box keeps it hidden as you paste."
  $key = Prompt-Secret 'Podocracy setup' 'Paste your OpenAI API key (leave blank to add it later):'
  if ($null -eq $key) {
    Show-Info 'Setup paused' "No problem - open Podocracy again whenever you're ready to add your OpenAI API key."
    exit 0
  }

  Write-EnvFile $home $key
  if ([string]::IsNullOrWhiteSpace($key)) {
    Show-Alert 'Add your key later' "Podocracy will start, but jobs need an OpenAI API key. Add it any time in: $(Join-Path $home '.env')"
  }
}

function Start-Stack($home, $logFile) {
  Push-Location $home
  try {
    & docker compose -f $ComposeFile up -d *>> $logFile
    if ($LASTEXITCODE -ne 0) {
      & docker-compose -f $ComposeFile up -d *>> $logFile
      if ($LASTEXITCODE -ne 0) {
        Show-Alert 'Could not start Podocracy' "Starting the containers failed. Details are in the log file: $logFile"
        exit 1
      }
    }
  }
  finally {
    Pop-Location
  }

  $timeout = if ($env:PODOCRACY_LAUNCH_TIMEOUT) { [int]$env:PODOCRACY_LAUNCH_TIMEOUT } else { 90 }
  $ready = $false
  for ($i = 0; $i -lt $timeout; $i++) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/health" -TimeoutSec 2 | Out-Null
      $ready = $true
      break
    }
    catch { Start-Sleep -Seconds 1 }
  }

  if (-not $ready) {
    Show-Alert 'Almost ready' "Podocracy is taking longer than usual, likely still downloading components on first run. We'll open the page now - refresh in a minute if it isn't ready yet."
  }

  Start-Process $Url
}

# --- main ---
Ensure-Docker
$PodocracyHome = Resolve-Home
$LogFile = Join-Path $PodocracyHome 'logs\launch.log'
Ensure-Home $PodocracyHome

# Respect an existing setup's projects dir / port instead of forcing our defaults.
if ($ProjectsEnv) {
  $env:PODOCRACY_PROJECTS_DIR = $ProjectsEnv
}
elseif (Get-EnvValue (Join-Path $PodocracyHome '.env') 'PODOCRACY_PROJECTS_DIR') {
  Remove-Item Env:\PODOCRACY_PROJECTS_DIR -ErrorAction SilentlyContinue
}
else {
  $env:PODOCRACY_PROJECTS_DIR = Join-Path $PodocracyHome 'projects'
}

if (-not $PortEnv) {
  $p = Get-EnvValue (Join-Path $PodocracyHome '.env') 'PORTAL_HTTP_PORT'
  if ($p) { $Port = $p; $Url = "http://localhost:$Port" }
}

Ensure-Env $PodocracyHome
Start-Stack $PodocracyHome $LogFile
exit 0
