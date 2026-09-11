Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$valuesFile = Join-Path $repositoryRoot "environments\stage\values\ingress-nginx.yaml"
$valuesText = Get-Content -Raw -Path $valuesFile

foreach ($requiredPattern in @(
  '(?ms)scope:\s*\r?\n\s*enabled:\s*true\s*\r?\n\s*namespace:\s*online-shop-stage',
  '(?ms)service:\s*\r?\n\s*enabled:\s*true\s*\r?\n\s*type:\s*ClusterIP\s*\r?\n\s*external:\s*\r?\n\s*enabled:\s*false',
  '(?ms)metrics:\s*\r?\n\s*enabled:\s*true',
  '(?ms)serviceMonitor:\s*\r?\n\s*#.*\r?\n\s*enabled:\s*false'
)) {
  if ($valuesText -notmatch $requiredPattern) {
    throw "Staging ingress bootstrap invariant is missing: $requiredPattern"
  }
}

Write-Output "Staging ingress bootstrap guardrails passed."
