# 生产启动（Windows）：单 worker + 信任反向代理头，禁止 --reload
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) { $pythonExe = 'python' }

$listenHost = if ($env:MAIC_HOST) { $env:MAIC_HOST } elseif ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$listenPort = if ($env:MAIC_PORT) { $env:MAIC_PORT } elseif ($env:PORT) { $env:PORT } else { '8001' }
# 仅当 Nginx 与后端在同一台机器时保留默认值；跨机器请改成 Nginx 的 IP
$forwardedIps = if ($env:FORWARDED_ALLOW_IPS) { $env:FORWARDED_ALLOW_IPS } else { '127.0.0.1' }

& $pythonExe -m uvicorn app.main:app `
  --host $listenHost --port $listenPort --workers 1 `
  --proxy-headers --forwarded-allow-ips $forwardedIps --log-level info
