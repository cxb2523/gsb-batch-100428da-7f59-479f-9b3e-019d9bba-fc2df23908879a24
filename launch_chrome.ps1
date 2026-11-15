# 启动一个带 CDP 调试端口的专用 Chrome 实例（端口 9333）
# 用独立 user-data-dir，与日常浏览器隔离；登录态持久保存在此目录。
# 用法: 右键“使用 PowerShell 运行”，或： powershell -File launch_chrome.ps1

$ErrorActionPreference = 'Stop'

$port = 9333
$profileDir = Join-Path $PSScriptRoot 'data\chrome-profile'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Error '未找到 Chrome，请手动修改脚本中的路径。'
    exit 1
}

Write-Host "启动 Chrome: $chrome"
Write-Host "调试端口: $port"
Write-Host "专用配置目录: $profileDir"
Write-Host '在弹出的浏览器里登录番茄小说；之后运行 python src/probe.py'

Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=$port",
    "--user-data-dir=$profileDir",
    "https://fanqienovel.com"
)
