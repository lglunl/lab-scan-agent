param(
    [Parameter(Mandatory = $true)]
    [string]$To
)

# 通过当前 Windows 用户的 DPAPI 解密授权码，并在同一 PowerShell 进程中运行邮件测试。
# 授权码不会写入命令行参数、配置文件或终端输出。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
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
        throw '不能解密邮箱授权码：请使用配置邮箱时相同的 Windows 用户运行，或重新执行 scripts/configure-mail.ps1'
    }
}

if (-not $env:LAB_SMTP_PASSWORD) {
    throw '没有读取到 SMTP 授权码。请先运行 scripts/configure-mail.ps1 配置邮箱。'
}

Set-Location $root
Write-Host '已从当前 Windows 用户的 DPAPI 文件读取授权码（不会显示授权码）。'
& python -m labcheckin.mail_cli check
if ($LASTEXITCODE -ne 0) { throw '邮件配置检查失败' }
& python -m labcheckin.mail_cli test --to $To
exit $LASTEXITCODE
