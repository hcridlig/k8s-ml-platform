# Remplace le jeton GITHUB_OWNER par ton identifiant GitHub dans overlays/ et gitops/ (une seule fois, après avoir créé ton dépôt).
#   ./scripts/set-owner.ps1 -Owner hugo-dupont                      (dépôt nommé k8s-ml-platform)
#   ./scripts/set-owner.ps1 -Owner hugo-dupont -Repo autre-nom
# Pourquoi : ArgoCD doit savoir quel dépôt Git surveiller, et Kustomize quel registre GHCR contient tes images.
# Les noms d'image GHCR doivent être en minuscules ; l'URL du dépôt garde ta casse.
param([Parameter(Mandatory)][string]$Owner, [string]$Repo = "k8s-ml-platform")
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding $false   # sans BOM : Kustomize et ArgoCD n'aiment pas
foreach ($f in Get-ChildItem overlays, gitops -Recurse -Filter *.yaml) {
    $old = [IO.File]::ReadAllText($f.FullName)
    $new = $old.Replace("ghcr.io/GITHUB_OWNER/", "ghcr.io/$($Owner.ToLower())/").Replace("GITHUB_OWNER/k8s-ml-platform", "$Owner/$Repo")
    if ($new -ne $old) { [IO.File]::WriteAllText($f.FullName, $new, $utf8); Write-Host "modifié : $($f.FullName)" }
}
