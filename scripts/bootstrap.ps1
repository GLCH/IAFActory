<#
  Installe les prerequis manquants via winget (source winget uniquement, pas msstore).
  Par defaut : simulation (-WhatIf implicite). Passer -Apply pour installer.
  Ne demarre pas Docker Desktop et n'accepte aucune licence a la place de l'utilisateur :
  Docker Desktop demande son propre accord au premier lancement.
#>
[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'

# commande verifiee -> identifiant winget (verifies avec `winget show --exact --source winget`)
$prereqs = [ordered]@{
    git    = 'Git.Git'
    gh     = 'GitHub.cli'
    docker = 'Docker.DockerDesktop'
    node   = 'OpenJS.NodeJS.LTS'
    python = 'Python.Python.3.13'
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget introuvable : installer 'App Installer' depuis le Microsoft Store."
}

foreach ($cmd in $prereqs.Keys) {
    $id = $prereqs[$cmd]
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Host ("{0,-8} deja present" -f $cmd)
        continue
    }
    if ($Apply) {
        Write-Host ("{0,-8} installation de {1}" -f $cmd, $id)
        winget install --id $id --exact --source winget --disable-interactivity `
            --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw "echec de l'installation de $id" }
    } else {
        Write-Host ("{0,-8} manquant : installerait {1} (relancer avec -Apply)" -f $cmd, $id)
    }
}

Write-Host "`nEtapes suivantes : ouvrir un nouveau terminal (PATH), lancer Docker Desktop,"
Write-Host "puis 'gh auth login', 'scripts/init-env.ps1' et 'scripts/doctor.ps1'."
