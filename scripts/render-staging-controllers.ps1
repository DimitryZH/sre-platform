param(
  [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$argoVersion = "7.8.28"
$ingressVersion = "4.12.1"
$kubernetesVersion = "1.35.7"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sre-platform-controller-render-" + [guid]::NewGuid().ToString())

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
  $OutputDirectory = Join-Path $temporaryRoot "rendered"
}

try {
  $configDirectory = Join-Path $temporaryRoot "config"
  $cacheDirectory = Join-Path $temporaryRoot "cache"
  $dataDirectory = Join-Path $temporaryRoot "data"
  $chartsDirectory = Join-Path $temporaryRoot "charts"
  New-Item -ItemType Directory -Path $configDirectory, $cacheDirectory, $dataDirectory, $chartsDirectory, $OutputDirectory -Force | Out-Null

  $env:HELM_REPOSITORY_CONFIG = Join-Path $configDirectory "repositories.yaml"
  $env:HELM_REPOSITORY_CACHE = $cacheDirectory
  $env:HELM_CONFIG_HOME = $configDirectory
  $env:HELM_DATA_HOME = $dataDirectory

  & helm repo add argo https://argoproj.github.io/argo-helm | Out-Null
  & helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx | Out-Null
  & helm pull argo/argo-cd --version $argoVersion --untar --untardir $chartsDirectory | Out-Null
  & helm pull ingress-nginx/ingress-nginx --version $ingressVersion --untar --untardir $chartsDirectory | Out-Null

  $argoValues = Join-Path $repositoryRoot "environments\stage\values\argocd.yaml"
  $ingressValues = Join-Path $repositoryRoot "environments\stage\values\ingress-nginx.yaml"
  $argoChart = Join-Path $chartsDirectory "argo-cd"
  $ingressChart = Join-Path $chartsDirectory "ingress-nginx"
  $argoOutput = Join-Path $OutputDirectory "argocd.yaml"
  $ingressOutput = Join-Path $OutputDirectory "ingress-nginx.yaml"

  & helm lint $argoChart -f $argoValues | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Argo CD lint failed." }
  & helm lint $ingressChart -f $ingressValues | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "ingress-nginx lint failed." }

  $argoRender = & helm template argo-cd $argoChart --namespace argocd --kube-version $kubernetesVersion --include-crds -f $argoValues
  if ($LASTEXITCODE -ne 0) { throw "Argo CD render failed." }
  $argoRenderText = $argoRender -join [Environment]::NewLine
  [System.IO.File]::WriteAllText($argoOutput, (($argoRender -join [Environment]::NewLine) + [Environment]::NewLine))

  $ingressRender = & helm template ingress-nginx $ingressChart --namespace ingress-nginx --kube-version $kubernetesVersion --include-crds -f $ingressValues
  if ($LASTEXITCODE -ne 0) { throw "ingress-nginx render failed." }
  [System.IO.File]::WriteAllText($ingressOutput, (($ingressRender -join [Environment]::NewLine) + [Environment]::NewLine))

  $combinedRender = (Get-Content -Raw -Path $argoOutput), (Get-Content -Raw -Path $ingressOutput) -join "`n---`n"
  foreach ($forbiddenPattern in @('(?m)^kind: PersistentVolumeClaim$', '(?m)^kind: Ingress$', '(?ms)^kind: Service\r?\n.*?^spec:\r?\n\s*type:\s*LoadBalancer\s*$')) {
    if ($combinedRender -match $forbiddenPattern) {
      throw "Unexpected rendered resource matched: $forbiddenPattern"
    }
  }

  $workloadDocuments = $combinedRender -split "(?m)^---\s*$" | Where-Object { $_ -match '(?m)^kind: (Deployment|StatefulSet)$' }
  foreach ($forbiddenWorkload in @('dex-server', 'notifications-controller')) {
    if ($workloadDocuments -match "(?m)^  name: .*${forbiddenWorkload}$") {
      throw "Disabled Argo CD component workload was rendered: $forbiddenWorkload"
    }
  }

  $applicationSetPattern = '(?ms)^kind: Deployment\r?\n.*?^  name: .*applicationset-controller\r?\n.*?^  replicas: 0\r?$'
  if ($combinedRender -notmatch $applicationSetPattern) {
    throw "The ApplicationSet controller must render exactly once with zero replicas."
  }

  if ($argoRenderText -notmatch '(?ms)resource\.customizations\.ignoreDifferences\.apps_Deployment:\s*\|.*?\.status\.terminatingReplicas') {
    throw "The Argo CD Deployment status compatibility customization must render."
  }

  $ingressDocuments = (Get-Content -Raw -Path $ingressOutput) -split "(?m)^---\s*$"
  $ingressServiceMonitors = @($ingressDocuments | Where-Object { $_ -match '(?m)^kind: ServiceMonitor$' })
  if ($ingressServiceMonitors.Count -ne 0) {
    throw "The initial ingress-nginx installation must not render a ServiceMonitor."
  }

  $metricsService = @($ingressDocuments | Where-Object {
    $_ -match '(?m)^kind: Service\r?$' -and
    $_ -match '(?m)^  name: ingress-nginx-controller-metrics\r?$' -and
    $_ -match '(?m)^    - name: metrics\r?$' -and
    $_ -match '(?m)^      port: 10254\r?$'
  })
  if ($metricsService.Count -ne 1) {
    throw "The initial ingress-nginx render must contain exactly one metrics Service with the reviewed contract."
  }

  $ingressValuesText = Get-Content -Raw -Path $ingressValues
  if ($ingressValuesText -notmatch '(?ms)metrics:\s*\r?\n\s*enabled:\s*true' -or
      $ingressValuesText -notmatch '(?ms)serviceMonitor:\s*\r?\n\s*#.*\r?\n\s*enabled:\s*false') {
    throw "Ingress metrics must remain enabled while the initial ServiceMonitor is disabled."
  }

  Write-Output "Pinned controller rendering passed with isolated Helm configuration."
  Write-Output "Initial ingress-nginx render has metrics and no ServiceMonitor."
  Write-Output "Argo CD render: $argoOutput"
  Write-Output "ingress-nginx render: $ingressOutput"
}
finally {
  Remove-Item Env:HELM_REPOSITORY_CONFIG -ErrorAction SilentlyContinue
  Remove-Item Env:HELM_REPOSITORY_CACHE -ErrorAction SilentlyContinue
  Remove-Item Env:HELM_CONFIG_HOME -ErrorAction SilentlyContinue
  Remove-Item Env:HELM_DATA_HOME -ErrorAction SilentlyContinue
  if ([string]::IsNullOrWhiteSpace($PSBoundParameters['OutputDirectory'])) {
    Remove-Item -Recurse -Force -LiteralPath $temporaryRoot -ErrorAction SilentlyContinue
  }
}
