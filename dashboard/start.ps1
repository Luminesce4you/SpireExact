param([switch]$NoBrowser, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
$serviceUrl = 'http://127.0.0.1:8765'
$mutex = New-Object System.Threading.Mutex($false, 'Local\SpireBoard_STS2_Launcher')
$acquired = $false
function Test-SpireBoard {
    try {
        $health = Invoke-RestMethod -Uri "$serviceUrl/api/health" -TimeoutSec 2
        return ($health.service -eq 'spireboard' -and $health.api_version -eq 1)
    } catch { return $false }
}
try {
    $acquired = $mutex.WaitOne(20000)
    if (-not $acquired) { throw '启动器正在运行，请稍后重试。' }
    if (-not (Test-SpireBoard)) {
        if (-not (Test-Path -LiteralPath $pythonExe)) { throw "找不到 Python 运行环境：$pythonExe" }
        $runtimeDir = Join-Path $PSScriptRoot 'runtime'
        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        $env:PYTHONIOENCODING = 'utf-8'
        $setupConfig = Join-Path $projectRoot '.tools\source-setup.json'
        if (Test-Path -LiteralPath $setupConfig) {
            $sourceConfig = Get-Content -LiteralPath $setupConfig -Raw | ConvertFrom-Json
            $sourceSdkDir = Split-Path -Parent $sourceConfig.dotnet
            $env:DOTNET_ROOT = $sourceSdkDir
            $env:PATH = $sourceSdkDir + [IO.Path]::PathSeparator + $env:PATH
        }
        $serverPath = Join-Path $PSScriptRoot 'server.py'
        Start-Process -FilePath $pythonExe -ArgumentList @(('"' + $serverPath + '"'), '--port', '8765') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeDir 'server.stderr.log') | Out-Null
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if (Test-SpireBoard) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "后端未能启动。请查看 $runtimeDir\server.stderr.log" }
    }
    if (-not $NoBrowser) { Start-Process -FilePath $serviceUrl | Out-Null }
    Write-Output $serviceUrl
} catch {
    if ($NoBrowser) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'SpireBoard 启动失败') | Out-Null
    exit 1
} finally {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
