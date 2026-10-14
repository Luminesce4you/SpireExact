param(
    [switch]$NoBrowser,
    [Alias('Python')][string]$PythonExe,
    [ValidateRange(1024,65535)][int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PythonExe) { $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source }
& $PythonExe -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12+，请用 -PythonExe 指定解释器。' }
$serviceUrl = "http://127.0.0.1:$Port"
$mutex = New-Object System.Threading.Mutex($false, "Local\SpireBoard_Source_Launcher_$Port")
$acquired = $false
function Get-ServiceHealth {
    $response = $null
    try {
        $request = [System.Net.WebRequest]::Create("$serviceUrl/api/health")
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        try { return ($reader.ReadToEnd() | ConvertFrom-Json) }
        finally { $reader.Dispose() }
    } catch { return $null }
    finally { if ($response) { $response.Dispose() } }
}
function Test-OwnService {
    $health = Get-ServiceHealth
    if (-not $health) { return $false }
    if ($health.service -ne 'spireboard' -or $health.api_version -ne 1 -or
        -not $health.workspace -or $health.workspace -ne $projectRoot) {
        throw "端口 $Port 已被其他服务或工作区占用；请用 -Port 选择另一个端口。"
    }
    return $true
}
try {
    $acquired = $mutex.WaitOne(20000)
    if (-not $acquired) { throw '启动器正在运行，请稍后重试。' }
    if (-not (Test-OwnService)) {
        $runtimeDir = Join-Path $PSScriptRoot 'runtime'
        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $setupFile = Join-Path $projectRoot '.tools/source-setup.json'
        if (Test-Path -LiteralPath $setupFile) {
            $setup = Get-Content -LiteralPath $setupFile -Raw | ConvertFrom-Json
            if ($setup.dotnet -and (Test-Path -LiteralPath $setup.dotnet)) {
                $sdkDirectory = Split-Path -Parent $setup.dotnet
                $env:DOTNET_ROOT = $sdkDirectory
                $env:PATH = $sdkDirectory + ';' + $env:PATH
            }
        }
        $serverPath = Join-Path $PSScriptRoot 'server.py'
        Start-Process -FilePath $PythonExe -ArgumentList @(('"' + $serverPath + '"'), '--port', "$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeDir 'server.stderr.log') | Out-Null
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if (Test-OwnService) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "后端未能启动。请查看 $runtimeDir\server.stderr.log" }
    }
    if (-not $NoBrowser) { Start-Process -FilePath $serviceUrl -WindowStyle Hidden | Out-Null }
    Write-Output $serviceUrl
} finally {
    if ($acquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
