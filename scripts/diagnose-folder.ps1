param(
    [ValidateSet('diagnose', 'preview')]
    [string]$Action = 'diagnose'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$portable = Join-Path $root 'runtime\python.exe'
if (Test-Path $portable) {
    & $portable -m labcheckin.folder_cli $Action
} else {
    & python -m labcheckin.folder_cli $Action
}
exit $LASTEXITCODE
