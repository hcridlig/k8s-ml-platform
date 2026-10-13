# Construit les deux images du projet et les charge dans les nœuds du cluster kind de Docker Desktop.
# Pourquoi le chargement : les nœuds kind sont des conteneurs avec leur PROPRE stockage d'images. Une image construite
# par `docker build` n'y est pas visible tant qu'elle n'est pas chargée (sinon : ErrImagePull / ImagePullBackOff).
# Avec un vrai cluster, cette étape serait un `docker push` vers un registry.
#
# Usage, depuis la racine du dépôt :  ./scripts/build.ps1            (ou -Cluster <nom> si plusieurs clusters kind)
param([string]$Cluster = "")
$ErrorActionPreference = "Stop"

function Run {
    & $args[0] $args[1..($args.Length - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Échec : $($args -join ' ')" }
}

if (-not $Cluster) { $Cluster = (kind get clusters | Select-Object -First 1) }
if (-not $Cluster) { throw "Aucun cluster kind trouvé (kind get clusters). Docker Desktop > Settings > Kubernetes doit être activé." }

# Mode manuel uniquement (sans GitHub). En GitOps, c'est la CI qui construit et publie les images.
# Les tags doivent correspondre à ceux de deploy/10-deployment.yaml et deploy/60-dashboard.yaml
Run docker build -t ml-api:1.2.0 ./app
Run docker build -t ml-dashboard:1.0.0 ./dashboard
Run kind load docker-image ml-api:1.2.0 ml-dashboard:1.0.0 --name $Cluster

Write-Host "Images chargées dans le cluster '$Cluster'."
