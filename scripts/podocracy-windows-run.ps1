<#
.SYNOPSIS
  Podocracy desktop launcher for Windows (no terminal required).

.DESCRIPTION
  The Windows counterpart to Podocracy.app. It:
    1. makes sure Docker Desktop is installed and running,
    2. creates the app-home folder (%USERPROFILE%\Podocracy) and downloads the compose file,
    3. asks for the OpenAI API key on first run and writes .env for the user,
    4. starts the containers, waits for /api/health, opens the browser, and exits.

  Run it invisibly from a shortcut so it feels like an app:
    powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File podocracy-windows-run.ps1
#>

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null

$PodocracyHome = if ($env:PODOCRACY_HOME) { $env:PODOCRACY_HOME } else { Join-Path $env:USERPROFILE 'Podocracy' }
$RawBase       = if ($env:PODOCRACY_RAW_BASE) { $env:PODOCRACY_RAW_BASE } else { 'https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main' }
$ComposeFile   = if ($env:PODOCRACY_COMPOSE_IMAGES_FILE) { $env:PODOCRACY_COMPOSE_IMAGES_FILE } else { 'docker-compose.images.yml' }
$Port          = if ($env:PORTAL_HTTP_PORT) { $env:PORTAL_HTTP_PORT } else { '8080' }
$Url           = "http://localhost:$Port"
$DockerUrl     = 'https://www.docker.com/products/docker-desktop/'
$OpenAiKeysUrl = 'https://platform.openai.com/api-keys'
$LogFile       = Join-Path $PodocracyHome 'logs\launch.log'

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

function Ensure-Home {
  New-Item -ItemType Directory -Force -Path (Join-Path $PodocracyHome 'projects') | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $PodocracyHome 'logs') | Out-Null

  $composePath = Join-Path $PodocracyHome $ComposeFile
  if (-not (Test-Path $composePath)) {
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$RawBase/$ComposeFile" -OutFile $composePath
    }
    catch {
      Show-Alert 'Download failed' "Couldn't download the Podocracy configuration. Check your internet connection and open Podocracy again."
      exit 1
    }
  }
}

function Write-EnvFile($key) {
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
  Set-Content -Path (Join-Path $PodocracyHome '.env') -Value $content -Encoding ascii
}

function Ensure-Env {
  if (Test-Path (Join-Path $PodocracyHome '.env')) { return }

  Show-Info 'Welcome to Podocracy' "First, let's add your OpenAI API key so Podocracy can transcribe and voice your projects. You can get a key at $OpenAiKeysUrl. The next box keeps it hidden as you paste."
  $key = Prompt-Secret 'Podocracy setup' 'Paste your OpenAI API key (leave blank to add it later):'
  if ($null -eq $key) {
    Show-Info 'Setup paused' "No problem - open Podocracy again whenever you're ready to add your OpenAI API key."
    exit 0
  }

  Write-EnvFile $key
  if ([string]::IsNullOrWhiteSpace($key)) {
    Show-Alert 'Add your key later' "Podocracy will start, but jobs need an OpenAI API key. Add it any time in: $(Join-Path $PodocracyHome '.env')"
  }
}

function Start-Stack {
  Push-Location $PodocracyHome
  try {
    & docker compose -f $ComposeFile up -d *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
      & docker-compose -f $ComposeFile up -d *>> $LogFile
      if ($LASTEXITCODE -ne 0) {
        Show-Alert 'Could not start Podocracy' "Starting the containers failed. Details are in the log file: $LogFile"
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

Ensure-Docker
Ensure-Home
$env:PODOCRACY_PROJECTS_DIR = if ($env:PODOCRACY_PROJECTS_DIR) { $env:PODOCRACY_PROJECTS_DIR } else { Join-Path $PodocracyHome 'projects' }
Ensure-Env
Start-Stack
exit 0
