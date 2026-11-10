# 新设备电脑不需要预先安装 Python。
# 第一次运行先弹出窗口。设备 ID 留空，以这次填入的设备 ID 和密钥覆盖 config.json，再启动。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'runtime'

function Get-ConfigString([string]$Text, [string]$Name) {
    $pattern = '"' + [regex]::Escape($Name) + '"\s*:\s*"((?:\\.|[^"\\])*)"'
    $match = [regex]::Match($Text, $pattern)
    if (-not $match.Success) { return '' }
    return $match.Groups[1].Value.Replace('\"', '"').Replace('\\', '\')
}

function Set-ConfigString([string]$Text, [string]$Name, [string]$Value) {
    $escaped = $Value.Replace('\', '\\').Replace('"', '\"')
    $pattern = '"' + [regex]::Escape($Name) + '"\s*:\s*"(?:\\.|[^"\\])*"'
    $replacement = '"' + $Name + '": "' + $escaped + '"'
    $updated = [regex]::Replace($Text, $pattern, $replacement, 1)
    if ($updated -eq $Text) { throw "config.json 里没有 $Name" }
    return $updated
}

function Save-DeviceSetup([string]$ConfigPath) {
    $marker = Join-Path $root 'data\.device-setup'
    if (Test-Path $marker) { return }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $text = [System.IO.File]::ReadAllText($ConfigPath, $utf8)
    $token = Get-ConfigString $text 'booking_api_token'

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object System.Windows.Forms.Form
    $form.Text = '连接预约系统'
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true
    $form.ClientSize = New-Object System.Drawing.Size(440, 250)
    $form.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = "填写这台仪器在预约系统里的设备 ID，以及预约系统的密钥。两项都要填，保存后才会启动。"
    $hint.Location = New-Object System.Drawing.Point(20, 16)
    $hint.Size = New-Object System.Drawing.Size(400, 44)

    $idLabel = New-Object System.Windows.Forms.Label
    $idLabel.Text = '设备 ID'
    $idLabel.Location = New-Object System.Drawing.Point(20, 68)
    $idLabel.AutoSize = $true
    $idBox = New-Object System.Windows.Forms.TextBox
    $idBox.Location = New-Object System.Drawing.Point(20, 92)
    $idBox.Size = New-Object System.Drawing.Size(400, 28)
    $idBox.Text = ''

    $tokenLabel = New-Object System.Windows.Forms.Label
    $tokenLabel.Text = '密钥'
    $tokenLabel.Location = New-Object System.Drawing.Point(20, 128)
    $tokenLabel.AutoSize = $true
    $tokenBox = New-Object System.Windows.Forms.TextBox
    $tokenBox.Location = New-Object System.Drawing.Point(20, 152)
    $tokenBox.Size = New-Object System.Drawing.Size(400, 28)
    $tokenBox.Text = $token

    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = '保存并启动'
    $ok.Location = New-Object System.Drawing.Point(210, 200)
    $ok.Size = New-Object System.Drawing.Size(100, 30)
    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = '取消'
    $cancel.Location = New-Object System.Drawing.Point(320, 200)
    $cancel.Size = New-Object System.Drawing.Size(100, 30)
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $ok.Add_Click({
        if (-not $idBox.Text.Trim() -or -not $tokenBox.Text.Trim()) {
            [System.Windows.Forms.MessageBox]::Show('设备 ID 和密钥都要填写。', '连接预约系统')
            return
        }
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    }.GetNewClosure())
    $form.AcceptButton = $ok
    $form.CancelButton = $cancel
    $form.Controls.AddRange(@($hint, $idLabel, $idBox, $tokenLabel, $tokenBox, $ok, $cancel))

    $result = $form.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        throw '还没有填写设备 ID 和密钥，扫码程序没有启动。'
    }
    $text = Set-ConfigString $text 'instrument_id' $idBox.Text.Trim()
    $text = Set-ConfigString $text 'booking_api_token' $tokenBox.Text.Trim()
    [System.IO.File]::WriteAllText($ConfigPath, $text, $utf8)
    New-Item -ItemType Directory -Force -Path (Split-Path $marker) | Out-Null
    Set-Content -Path $marker -Value (Get-Date -Format o) -Encoding ascii
    Write-Host '设备 ID 和密钥已写入 config.json'
}

$configPath = Join-Path $root 'config.json'
if (-not (Test-Path $configPath)) { throw '找不到 config.json' }
Save-DeviceSetup $configPath
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
