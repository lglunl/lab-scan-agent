# 由 start-device.ps1 / test-mail.ps1 调用；从随项目复制的 mail.env 加载统一 SMTP 配置。
# 仅允许设置 SMTP 相关变量，不执行 mail.env 中的任意 PowerShell / CMD 代码。
$root = Split-Path -Parent $PSScriptRoot
$mailEnvPath = Join-Path $root 'mail.env'
if (Test-Path $mailEnvPath) {
    $allowed = @(
        'LAB_SMTP_HOST',
        'LAB_SMTP_PORT',
        'LAB_SMTP_SECURITY',
        'LAB_SMTP_USER',
        'LAB_SMTP_FROM',
        'LAB_SMTP_PASSWORD'
    )
    foreach ($rawLine in [System.IO.File]::ReadAllLines($mailEnvPath, [System.Text.Encoding]::UTF8)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { continue }
        $key = $line.Substring(0, $separator).Trim()
        if ($allowed -notcontains $key) { continue }
        $value = $line.Substring($separator + 1).Trim()
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
    Write-Host '已加载项目统一邮箱配置 mail.env（授权码不会显示）。'
}
