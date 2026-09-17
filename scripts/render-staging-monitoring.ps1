param(
  [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$chartVersion = "65.5.1"
$kubernetesVersion = "1.35.7"
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sre-platform-monitoring-render-" + [guid]::NewGuid().ToString())

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

  & helm repo add prometheus-community https://prometheus-community.github.io/helm-charts | Out-Null
  & helm pull prometheus-community/kube-prometheus-stack --version $chartVersion --untar --untardir $chartsDirectory | Out-Null

  $chart = Join-Path $chartsDirectory "kube-prometheus-stack"
  $baseValues = Join-Path $repositoryRoot "observability\helm\kube-prometheus-stack\values.yaml"
  $sharedValues = Join-Path $repositoryRoot "environments\stage\values\kube-prometheus-stack-shared.yaml"
  $stageValues = Join-Path $repositoryRoot "environments\stage\values\kube-prometheus-stack.yaml"
  $renderPath = Join-Path $OutputDirectory "monitoring.yaml"

  & helm lint $chart -f $baseValues -f $sharedValues -f $stageValues | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "kube-prometheus-stack lint failed." }

  $render = & helm template monitoring $chart --namespace monitoring --kube-version $kubernetesVersion --include-crds -f $baseValues -f $sharedValues -f $stageValues
  if ($LASTEXITCODE -ne 0) { throw "kube-prometheus-stack render failed." }
  $renderText = $render -join [Environment]::NewLine
  [System.IO.File]::WriteAllText($renderPath, ($renderText + [Environment]::NewLine))

  foreach ($forbiddenPattern in @(
    '(?m)^kind: PersistentVolumeClaim$',
    '(?m)^kind: Ingress$',
    '(?ms)^kind: Service\r?\n.*?^spec:\r?\n\s*type:\s*LoadBalancer\s*$',
    '(?m)^\s*routing_key:\s*'
  )) {
    if ($renderText -match $forbiddenPattern) {
      throw "Unexpected rendered monitoring resource or inline routing key."
    }
  }

  foreach ($requiredPattern in @(
    '(?m)^kind: Alertmanager\r?$',
    '(?m)^\s*serviceAccountName:\s*alertmanager-stage\s*$',
    '(?m)^\s*driver:\s*secrets-store-gke\.csi\.k8s\.io\s*$',
    '(?m)^\s*secretProviderClass:\s*alertmanager-stage-pagerduty\s*$'
  )) {
    if ($renderText -notmatch $requiredPattern) {
      throw "Rendered Alertmanager configuration is incomplete."
    }
  }

  $alertmanagerConfigDocument = @($renderText -split "(?m)^---\s*\r?$" | Where-Object {
    $_ -match '(?m)^kind: Secret\r?$' -and $_ -match '(?m)^  name: alertmanager-monitoring-kube-prometheus-alertmanager\r?$'
  })
  if ($alertmanagerConfigDocument.Count -ne 1) {
    throw "Expected generated Alertmanager configuration Secret was not rendered exactly once."
  }

  $configMatch = [regex]::Match($alertmanagerConfigDocument[0], '(?ms)^  alertmanager\.yaml:\s*"(?<data>.*?)"\s*$')
  if (-not $configMatch.Success) {
    throw "Rendered Alertmanager configuration could not be decoded."
  }
  $configData = $configMatch.Groups['data'].Value -replace '\s', ''
  $alertmanagerConfig = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($configData))

  foreach ($requiredConfigPattern in @(
    '(?m)^\s*routing_key_file:\s*/var/run/secrets/pagerduty/routing-key\s*$',
    '(?m)^\s*send_resolved:\s*true\s*$',
    '(?m)^\s*severity:\s*critical\s*$',
    '(?ms)send_resolved:\s*true\s*\r?\n\s*(?:#.*\r?\n\s*){0,2}severity:\s*critical',
    'alertname="OnlineShopSLOFastBurnRatePage"',
    'environment="staging"',
    'severity="page"'
  )) {
    if ($alertmanagerConfig -notmatch $requiredConfigPattern) {
      throw "Rendered Alertmanager receiver configuration is incomplete."
    }
  }

  if (($alertmanagerConfig | Select-String -AllMatches 'receiver:\s*pagerduty-staging').Matches.Count -ne 1) {
    throw "Rendered configuration must contain exactly one PagerDuty receiver route."
  }

  Write-Output "Pinned staging monitoring rendering passed with isolated Helm configuration."
  Write-Output "The rendered Alertmanager uses a Secret Manager CSI file reference without an inline routing key."
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
