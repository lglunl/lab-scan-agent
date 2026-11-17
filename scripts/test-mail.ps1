param(
    [Parameter(Mandatory = $true)]
    [string]$To
)

# 优先读取跨设备通用的 mail.env；兼容旧版 DPAPI 文件。授权码不会输出到终端。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'load-mail-env.ps1')
$protectedSmtp = Join-Path $root 'data\.smtp-password.dpapi'

if (-not $env:LAB_SMTP_PASSWORD -and (Test-Path $protectedSmtp)) {
    try {
        $encryptedText = (Get-Content -Raw -Path $protectedSmtp).Trim()
        $secureString = ConvertTo-SecureString -String $encryptedText
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureString)
        try {
            $env:LAB_SMTP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    } catch {
        throw '不能解密旧版邮箱授权码：请在主程序包重新执行 scripts/configure-mail.ps1 生成 mail.env'
    }
}

if (-not $env:LAB_SMTP_PASSWORD) {
    throw '没有读取到 SMTP 授权码。请先在主程序包运行 scripts/configure-mail.ps1 配置统一邮箱。'
}

Set-Location $root
Write-Host '已加载统一邮箱授权码（不会显示授权码）。'
& python -m labcheckin.mail_cli check
if ($LASTEXITCODE -ne 0) { throw '邮件配置检查失败' }
& python -m labcheckin.mail_cli test --to $To
exit $LASTEXITCODE
