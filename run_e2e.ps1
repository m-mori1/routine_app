$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root '.e2e.env'

if (-not (Test-Path $envFile)) {
  throw ".e2e.env が見つかりません: $envFile"
}

function Read-EnvFileLines {
  param([string]$Path)
  foreach ($enc in @('UTF8', 'Unicode', 'Default')) {
    try {
      return Get-Content $Path -Encoding $enc
    } catch {
      continue
    }
  }
  throw ".e2e.env の読み込みに失敗しました: $Path"
}

Read-EnvFileLines -Path $envFile | ForEach-Object {
  if ([string]::IsNullOrWhiteSpace($_)) { return }
  if ($_.TrimStart().StartsWith('#')) { return }
  $idx = $_.IndexOf('=')
  if ($idx -lt 1) { return }
  $name = $_.Substring(0, $idx).Trim()
  $value = $_.Substring($idx + 1)
  [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

function Resolve-NpmCommand {
  $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  foreach ($p in @(
    'C:\Program Files\nodejs\npm.cmd',
    'C:\Program Files (x86)\nodejs\npm.cmd',
    "$env:USERPROFILE\AppData\Roaming\npm\npm.cmd"
  )) {
    if (Test-Path $p) { return $p }
  }

  throw 'npm.cmd が見つかりません。Node.js LTS をインストールしてください。'
}

function Resolve-NodeDirectory {
  $nodeCmd = Get-Command node.exe -ErrorAction SilentlyContinue
  if ($nodeCmd) { return Split-Path -Parent $nodeCmd.Source }
  foreach ($p in @(
    'C:\Program Files\nodejs\node.exe',
    'C:\Program Files (x86)\nodejs\node.exe',
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe"
  )) {
    if (Test-Path $p) { return Split-Path -Parent $p }
  }
  throw 'node.exe が見つかりません。Node.js LTS をインストールしてください。'
}

$npm = Resolve-NpmCommand
$nodeDir = Resolve-NodeDirectory

# npm.cmd から node.exe が確実に見えるようにする
if (-not ($env:Path -split ';' | Where-Object { $_ -eq $nodeDir })) {
  $env:Path = "$nodeDir;$env:Path"
}

if ($root.StartsWith('\\')) {
  # npm/cmd の UNC パス制約を回避（pushd が一時ドライブを割り当てる）
  $cmdLine = "pushd `"$root`" && `"$npm`" run e2e:test && popd"
  cmd /c $cmdLine
  if ($LASTEXITCODE -ne 0) {
    throw "Playwright 実行に失敗しました (exit=$LASTEXITCODE)"
  }
} else {
  Set-Location $root
  & $npm run e2e:test
}
