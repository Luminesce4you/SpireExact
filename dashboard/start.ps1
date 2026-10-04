param([switch]$NoBrowser, [string]$Python = 'python', [ValidateRange(1024,65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
& $pythonExe -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 或更新版本；请用 -Python 指定所用的解释器。' }
$serviceUrl = "http://127.0.0.1:$Port"
$mutex = New-Object System.Threading.Mutex($false, "Local\SpireBoard_Source_Launcher_$Port")
$acquired = $false
function Get-SpireBoardHealth {
    $request = [System.Net.HttpWebRequest]::Create("$serviceUrl/api/health")
    $request.Proxy = $null
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}
function Test-SpireBoard {
    try {
        $health = Get-SpireBoardHealth
        return ($health.service -eq 'spireboard' -and $health.api_version -eq 1 -and $health.workspace -eq $projectRoot)
    } catch { return $false }
}
try {
    $acquired = $mutex.WaitOne(20000)
    if (-not $acquired) { throw '启动器正在运行，请稍后重试。' }
    if (-not (Test-SpireBoard)) {
        try {
            $other = Get-SpireBoardHealth
            if ($other.service -eq 'spireboard' -and $other.workspace -ne $projectRoot) {
                throw "端口 $Port 已用于其他工作区。请用 -Port 指定另一个端口。"
            }
        } catch {
            if ($_.Exception.Message -like '端口 *') { throw }
        }
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
        Start-Process -FilePath $pythonExe -ArgumentList @(('"' + $serverPath + '"'), '--port', [string]$Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeDir 'server.stderr.log') | Out-Null
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
