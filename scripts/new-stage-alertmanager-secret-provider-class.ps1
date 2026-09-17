param(
  [Parameter(Mandatory)]
  [string]$SecretResourceName,
  [string]$OutputPath = (Join-Path $PSScriptRoot "..\.private\alertmanager-stage-secret-provider-class.yaml")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($SecretResourceName -notmatch '^projects/[^/]+/secrets/[^/]+/versions/(latest|[0-9]+)$') {
  throw "SecretResourceName must be a fully-qualified Secret Manager version resource name."
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$privateRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot ".private"))
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)

if (-not $resolvedOutputPath.StartsWith($privateRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "OutputPath must remain under the ignored .private directory."
}

New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutputPath) -Force | Out-Null

# This manifest is deliberately local-only: it contains the operator-managed
# Secret Manager resource reference and must never be committed or logged.
$manifest = @"
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: alertmanager-stage-pagerduty
  namespace: monitoring
spec:
  provider: gke
  parameters:
    secrets: |
      - resourceName: "$SecretResourceName"
        path: "routing-key"
"@

[System.IO.File]::WriteAllText($resolvedOutputPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "Private Alertmanager SecretProviderClass manifest rendered."
