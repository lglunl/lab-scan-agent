# 在 Windows 仪器电脑上运行一次：非敏感 SMTP 参数写 config.json；授权码用当前用户 DPAPI 加密存储。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $root 'config.json'
$secretPath = Join-Path $root 'data\.smtp-password.dpapi'
if (-not (Test-Path $configPath)) { throw '找不到 config.json' }
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

Write-Host '配置实验室自动发信邮箱（输入为空表示保留原值）'
$hostName = Read-Host "SMTP 主机 [$($config.smtp_host)]"
$portText = Read-Host "SMTP 端口 [$($config.smtp_port)]"
$security = Read-Host "安全连接 starttls / ssl / none [$($config.smtp_security)]"
$user = Read-Host "SMTP 账号 [$($config.smtp_user)]"
$from = Read-Host "发信邮箱 [$($config.smtp_from)]"
if ($hostName.Trim()) { $config.smtp_host = $hostName.Trim() }
if ($portText.Trim()) {
    $portNumber = 0
    if (-not [int]::TryParse($portText.Trim(), [ref]$portNumber) -or $portNumber -lt 1 -or $portNumber -gt 65535) {
        throw 'SMTP 端口必须是 1-65535 之间的整数'
    }
    $config.smtp_port = $portNumber
}
if ($security.Trim()) {
    if ($security.Trim() -notin @('starttls','ssl','none')) { throw '安全连接只允许 starttls、ssl、none' }
    $config.smtp_security = $security.Trim()
}
if ($user.Trim()) { $config.smtp_user = $user.Trim() }
if ($from.Trim()) { $config.smtp_from = $from.Trim() }
# 清除配置文件中的历史 SMTP 明文密码；邮箱密码只存在 DPAPI 机密文件中。
$config.smtp_password = ''

$secret = Read-Host 'SMTP 应用授权码（输入后按回车；仅首次配置或修改时需要）' -AsSecureString
if ($secret.Length -gt 0) {
    New-Item -ItemType Directory -Force -Path (Split-Path $secretPath) | Out-Null
    $encrypted = ConvertFrom-SecureString -SecureString $secret
    Set-Content -Path $secretPath -Value $encrypted -Encoding ASCII
    Write-Host '已使用当前 Windows 用户的 DPAPI 加密保存邮箱授权码'
} elseif (-not (Test-Path $secretPath)) {
    Write-Warning '尚未保存 SMTP 密码；若需要登录 SMTP，请重新运行此脚本设置授权码'
}
$json = $config | ConvertTo-Json -Depth 16
[System.IO.File]::WriteAllText($configPath, $json + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host 'SMTP 配置已保存。可运行：python -m labcheckin.mail_cli check'
