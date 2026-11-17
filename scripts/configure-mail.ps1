# 在一台可信的配置电脑上运行一次，生成可随项目文件夹迁移的 mail.env。
# 注意：为了跨电脑复用，mail.env 中的 SMTP 授权码以明文保存；请只在可信设备间传递。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $root 'config.json'
$mailEnvPath = Join-Path $root 'mail.env'
$legacySecretPath = Join-Path $root 'data\.smtp-password.dpapi'
if (-not (Test-Path $configPath)) { throw '找不到 config.json' }
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json

$values = @{}
if (Test-Path $mailEnvPath) {
    foreach ($line in [System.IO.File]::ReadAllLines($mailEnvPath, [System.Text.Encoding]::UTF8)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $separator = $trimmed.IndexOf('=')
        if ($separator -gt 0) {
            $values[$trimmed.Substring(0, $separator).Trim()] = $trimmed.Substring($separator + 1).Trim()
        }
    }
}

function Get-DefaultValue([string]$Key, [string]$ConfigValue) {
    if ($values.ContainsKey($Key) -and $values[$Key]) { return [string]$values[$Key] }
    return $ConfigValue
}

Write-Host '配置实验室统一发信邮箱（只需在主程序包上配置一次，其他设备复制 mail.env 即可）'
$hostName = Read-Host "SMTP 主机 [$(Get-DefaultValue 'LAB_SMTP_HOST' ([string]$config.smtp_host))]"
$portText = Read-Host "SMTP 端口 [$(Get-DefaultValue 'LAB_SMTP_PORT' ([string]$config.smtp_port))]"
$security = Read-Host "安全连接 starttls / ssl / none [$(Get-DefaultValue 'LAB_SMTP_SECURITY' ([string]$config.smtp_security))]"
$user = Read-Host "SMTP 账号 [$(Get-DefaultValue 'LAB_SMTP_USER' ([string]$config.smtp_user))]"
$from = Read-Host "发信邮箱 [$(Get-DefaultValue 'LAB_SMTP_FROM' ([string]$config.smtp_from))]"

if (-not $hostName.Trim()) { $hostName = Get-DefaultValue 'LAB_SMTP_HOST' ([string]$config.smtp_host) }
if (-not $portText.Trim()) { $portText = Get-DefaultValue 'LAB_SMTP_PORT' ([string]$config.smtp_port) }
if (-not $security.Trim()) { $security = Get-DefaultValue 'LAB_SMTP_SECURITY' ([string]$config.smtp_security) }
if (-not $user.Trim()) { $user = Get-DefaultValue 'LAB_SMTP_USER' ([string]$config.smtp_user) }
if (-not $from.Trim()) { $from = Get-DefaultValue 'LAB_SMTP_FROM' ([string]$config.smtp_from) }

$portNumber = 0
if (-not [int]::TryParse($portText.Trim(), [ref]$portNumber) -or $portNumber -lt 1 -or $portNumber -gt 65535) {
    throw 'SMTP 端口必须是 1-65535 之间的整数'
}
$security = $security.Trim().ToLowerInvariant()
if ($security -notin @('starttls', 'ssl', 'none')) { throw '安全连接只允许 starttls、ssl、none' }

$password = [string]$values['LAB_SMTP_PASSWORD']
# 兼容旧版本：如果本机已有 DPAPI 密码文件，先尝试迁移，无需重新输入。
if (-not $password -and (Test-Path $legacySecretPath)) {
    try {
        $encryptedText = (Get-Content -Raw -Path $legacySecretPath).Trim()
        $secureString = ConvertTo-SecureString -String $encryptedText
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureString)
        try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        if ($password) { Write-Host '已从旧版本机 DPAPI 配置迁移授权码。' }
    } catch {
        Write-Warning '旧版 DPAPI 授权码无法迁移；请在下方重新输入一次。'
    }
}

$secret = Read-Host 'SMTP 应用授权码（留空则保留已有授权码）' -AsSecureString
if ($secret.Length -gt 0) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

$lines = @(
    '# 实验室统一 SMTP 配置；此文件包含授权码，请勿公开或提交到 Git。',
    "LAB_SMTP_HOST=$($hostName.Trim())",
    "LAB_SMTP_PORT=$portNumber",
    "LAB_SMTP_SECURITY=$security",
    "LAB_SMTP_USER=$($user.Trim())",
    "LAB_SMTP_FROM=$($from.Trim())",
    "LAB_SMTP_PASSWORD=$password"
)
[System.IO.File]::WriteAllText($mailEnvPath, ($lines -join "`r`n") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

if ($password) {
    Write-Host '统一邮箱配置已保存到 mail.env；授权码已设置（不会显示）。'
    Write-Host '以后将整个程序文件夹（包括 mail.env）复制到其他设备即可，不必重新配置邮箱。'
} else {
    Write-Warning 'mail.env 已生成，但尚未设置 SMTP 授权码；邮件发送功能暂不可用。'
}
Write-Warning '跨电脑复用需要可迁移凭据，因此 mail.env 中的授权码是明文。请只向可信设备分发，并限制文件夹访问权限。'
