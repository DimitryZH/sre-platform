Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$applicationsDirectory = Join-Path $repositoryRoot "environments\stage\argocd\apps"
$serviceMonitorFile = Join-Path $repositoryRoot "environments\stage\argocd\ingress-nginx-metrics\servicemonitor.yaml"

function Get-ApplicationText([string]$Name) {
  $path = Join-Path $applicationsDirectory $Name
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Missing staging Application: $Name"
  }

  return Get-Content -Raw -Path $path
}

foreach ($expectedWave in @{
  "monitoring-stage.yaml" = "-2"
  "argo-rollouts-stage.yaml" = "-1"
  "online-shop-stage.yaml" = "1"
  "ingress-nginx-metrics-stage.yaml" = "1"
}.GetEnumerator()) {
  $applicationText = Get-ApplicationText $expectedWave.Key
  $wavePattern = 'argocd\.argoproj\.io/sync-wave:\s*"' + [regex]::Escape($expectedWave.Value) + '"'
  if ($applicationText -notmatch $wavePattern) {
    throw "Incorrect sync wave for $($expectedWave.Key)."
  }
}

$monitoringText = Get-ApplicationText "monitoring-stage.yaml"
if ($monitoringText -notmatch 'chart:\s*kube-prometheus-stack\s*\r?\n\s*targetRevision:\s*65\.5\.1') {
  throw "Monitoring chart version drift detected."
}

$rolloutsText = Get-ApplicationText "argo-rollouts-stage.yaml"
if ($rolloutsText -notmatch 'chart:\s*argo-rollouts\s*\r?\n\s*targetRevision:\s*2\.32\.0') {
  throw "Argo Rollouts chart version drift detected."
}

$ingressMetricsApplicationText = Get-ApplicationText "ingress-nginx-metrics-stage.yaml"
if ($ingressMetricsApplicationText -notmatch 'path:\s*environments/stage/argocd/ingress-nginx-metrics') {
  throw "Ingress metrics Application must target only the reviewed ServiceMonitor path."
}

$serviceMonitorText = Get-Content -Raw -Path $serviceMonitorFile
function Test-ServiceMonitorScalar([string]$Key, [string]$Value) {
  $pattern = '(?m)^\s*(?:-\s*)?' + [regex]::Escape($Key) + '\s*:\s*' + [regex]::Escape($Value) + '\s*(?:#.*)?\r?$'
  return $serviceMonitorText -match $pattern
}

foreach ($requiredScalar in @(
  @{ Key = "apiVersion"; Value = "monitoring.coreos.com/v1" },
  @{ Key = "kind"; Value = "ServiceMonitor" },
  @{ Key = "namespace"; Value = "ingress-nginx" },
  @{ Key = "release"; Value = "monitoring" },
  @{ Key = "app.kubernetes.io/name"; Value = "ingress-nginx" },
  @{ Key = "app.kubernetes.io/instance"; Value = "ingress-nginx" },
  @{ Key = "app.kubernetes.io/component"; Value = "controller" },
  @{ Key = "port"; Value = "metrics" },
  @{ Key = "interval"; Value = "30s" }
)) {
  if (-not (Test-ServiceMonitorScalar $requiredScalar.Key $requiredScalar.Value)) {
    throw "Ingress metrics ServiceMonitor contract is incomplete: $($requiredScalar.Key)=$($requiredScalar.Value)"
  }
}

foreach ($forbiddenKind in @("Secret", "PersistentVolumeClaim", "Ingress", "Service", "Deployment", "StatefulSet", "DaemonSet")) {
  if ($serviceMonitorText -match "(?m)^kind:\s*$forbiddenKind$") {
    throw "Unexpected resource in the ingress metrics GitOps path: $forbiddenKind"
  }
}

foreach ($applicationName in @("monitoring-stage.yaml", "argo-rollouts-stage.yaml", "online-shop-stage.yaml")) {
  $applicationText = Get-ApplicationText $applicationName
  if ($applicationText -notmatch '(?ms)ignoreDifferences:\s*\r?\n\s*- group:\s*apps\s*\r?\n\s*kind:\s*Deployment\s*\r?\n\s*jqPathExpressions:\s*\r?\n\s*- \.status\.terminatingReplicas') {
    throw "Deployment status compatibility rule is missing from $applicationName."
  }
  if ($applicationText -notmatch 'RespectIgnoreDifferences=true') {
    throw "RespectIgnoreDifferences sync option is missing from $applicationName."
  }
}

$monitoringValuesFile = Join-Path $repositoryRoot "environments\stage\values\kube-prometheus-stack-shared.yaml"
$monitoringValuesText = Get-Content -Raw -Path $monitoringValuesFile
$monitoringProfileFile = Join-Path $repositoryRoot "environments\stage\values\kube-prometheus-stack.yaml"
$monitoringProfileText = Get-Content -Raw -Path $monitoringProfileFile
$sloRulesFile = Join-Path $repositoryRoot "charts\platform\templates\prometheus-rules.yaml"
$sloRulesText = Get-Content -Raw -Path $sloRulesFile

if ($monitoringProfileText -notmatch '(?ms)^kubeStateMetrics:\s*\r?\n\s*enabled:\s*false\s*$') {
  throw "The constrained monitoring profile must disable kube-state-metrics through kubeStateMetrics.enabled."
}

if ($sloRulesText -notmatch 'exported_namespace="\{\{ \$releaseNs \}\}"' -or
    $sloRulesText -match '(?<!exported_)namespace="\{\{ \$releaseNs \}\}"') {
  throw "Stage SLO rules must use the ServiceMonitor relabeled exported_namespace selector."
}

if ($monitoringValuesText -notmatch '(?ms)admissionWebhooks:\s*\r?\n\s*#.*\r?\n\s*#.*\r?\n\s*enabled:\s*false\s*\r?\n\s*patch:\s*\r?\n\s*enabled:\s*false') {
  throw "The constrained monitoring profile must disable admission webhooks and patch hooks."
}

if ($monitoringValuesText -notmatch '(?ms)prometheusOperator:\s*\r?\n\s*tls:\s*\r?\n\s*#.*\r?\n\s*#.*\r?\n\s*enabled:\s*false') {
  throw "The constrained monitoring profile must disable the operator TLS listener."
}

Write-Output "Staging GitOps dependency-order guardrails passed."
