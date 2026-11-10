# 新设备电脑不需要预先安装 Python。
# 第一次运行会下载便携版 Python 并安装依赖，之后直接启动扫码程序。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'runtime'
$python = Join-Path $runtime 'python.exe'
$version = '3.12.8'
$zipUrl = "https://www.python.org/ftp/python/$version/python-$version-embed-amd64.zip"

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Enable-SitePackages([string]$RuntimeDir) {
    $pth = Get-ChildItem -Path $RuntimeDir -Filter 'python*._pth' | Select-Object -First 1
    if (-not $pth) { throw '便携版 Python 里没有找到 python._pth' }
    $lines = @(Get-Content -Path $pth.FullName)
    $next = New-Object System.Collections.Generic.List[string]
    $seenSite = $false
    foreach ($line in $lines) {
        if ($line -eq '#import site') { continue }
        if ($line -eq 'Lib\site-packages') { $seenSite = $true }
        if ($line -eq 'import site') { continue }
        $next.Add($line)
    }
    if (-not $seenSite) { $next.Add('Lib\site-packages') }
    $next.Add('import site')
    Set-Content -Path $pth.FullName -Value $next -Encoding ascii
}

if (-not (Test-Path $python)) {
    Write-Host '这台电脑没有可用的 Python，正在下载便携版（只需这一次）...'
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    $zip = Join-Path $runtime 'python-embed.zip'
    Invoke-WebRequest -Uri $zipUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $runtime -Force
    Remove-Item $zip
    Enable-SitePackages $runtime
}

$marker = Join-Path $runtime '.requirements-installed'
$requirements = Join-Path $root 'requirements.txt'
$needInstall = -not (Test-Path $marker)
if (-not $needInstall) {
    $needInstall = (Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $marker).LastWriteTimeUtc
}
if ($needInstall) {
    Write-Host '正在安装扫码程序依赖（只需这一次）...'
    New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'Lib\site-packages') | Out-Null
    $getPip = Join-Path $runtime 'get-pip.py'
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getPip
    & $python $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw '安装 pip 失败' }
    & $python -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
    Set-Content -Path $marker -Value (Get-Date -Format o) -Encoding ascii
}

Set-Location $root
$appPath = Join-Path $runtime 'Lib\site-packages\labcheckin-app.pth'
Set-Content -Path $appPath -Value $root -Encoding ascii
Write-Host '扫码程序已启动。不要关闭这个窗口。本机页面： http://127.0.0.1:8787'
& $python -m labcheckin
exit $LASTEXITCODE
