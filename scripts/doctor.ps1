<#
  Verifie les prerequis de la machine sans rien modifier.
  Code de sortie 1 si un prerequis obligatoire manque ou si le daemon Docker est arrete.
#>
$ErrorActionPreference = 'Continue'
$failed = $false

function Test-Tool($name, $versionArgs, [bool]$required = $true) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $tag = if ($required) { 'MANQUANT' } else { 'absent (optionnel)' }
        Write-Host ("{0,-16} {1}" -f $name, $tag)
        if ($required) { $script:failed = $true }
        return
    }
    $v = (& $name @versionArgs 2>$null | Select-Object -First 1)
    Write-Host ("{0,-16} OK  {1}" -f $name, $v)
}

Test-Tool git @('--version')
Test-Tool gh @('--version')
Test-Tool docker @('--version')
Test-Tool node @('--version')
Test-Tool python @('--version')

# Le daemon doit tourner, pas seulement le client.
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $server = docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $server) {
        Write-Host ("{0,-16} OK  daemon {1}" -f 'docker daemon', $server)
        docker compose version 2>$null | Select-Object -First 1 | ForEach-Object { Write-Host ("{0,-16} OK  {1}" -f 'docker compose', $_) }
    } else {
        Write-Host ("{0,-16} ARRETE (lancer Docker Desktop)" -f 'docker daemon')
        $failed = $true
    }
}

$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (Test-Path $envFile) { Write-Host ("{0,-16} OK" -f '.env') }
else { Write-Host ("{0,-16} absent : lancer scripts/init-env.ps1" -f '.env'); $failed = $true }

if ($failed) { Write-Host "`nPrerequis incomplets."; exit 1 }
Write-Host "`nPrerequis complets."
