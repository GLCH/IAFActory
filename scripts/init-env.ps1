<#
  Genere .env depuis .env.example avec des mots de passe aleatoires
  (alphanumeriques : ils sont injectes tels quels dans shiro.ini et Cypher).
  N'ecrase jamais un .env existant sauf avec -Force.
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$example = Join-Path $root '.env.example'
$target = Join-Path $root '.env'

if ((Test-Path $target) -and -not $Force) {
    Write-Host ".env existe deja, rien a faire (utiliser -Force pour regenerer)."
    exit 0
}

function New-Secret([int]$Length = 32) {
    $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $bytes = [byte[]]::new($Length)
    # Create().GetBytes : compatible Windows PowerShell 5.1 (.NET Framework n'a pas Fill).
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

$lines = Get-Content $example | ForEach-Object {
    if ($_ -match '^(POSTGRES_PASSWORD|NEO4J_PASSWORD|FUSEKI_ADMIN_PASSWORD)=$') {
        "$($Matches[1])=$(New-Secret)"
    } else { $_ }
}
Set-Content -Path $target -Value $lines -Encoding utf8
Write-Host ".env genere ($target). Il est ignore par git."
