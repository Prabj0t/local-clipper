param(
    [switch]$Setup,
    [switch]$Check,
    [switch]$Dev,
    [switch]$AllowNetwork,
    [switch]$Yes,
    [string]$Wheelhouse,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $arguments = @('-m', 'tools.bootstrap', '--port', "$Port")
    if ($Setup) { $arguments += '--setup' }
    if ($Check) { $arguments += '--check' }
    if ($Dev) { $arguments += '--dev' }
    if ($AllowNetwork) { $arguments += '--allow-network' }
    if ($Yes) { $arguments += '--yes' }
    if ($Wheelhouse) { $arguments += @('--wheelhouse', $Wheelhouse) }
    $local = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $local) {
        & $local @arguments
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 @arguments
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @arguments
    } else {
        throw 'Install Python 3.11 from an administrator-approved source first. Nothing will be downloaded automatically.'
    }
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
