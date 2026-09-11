param(
  [Parameter(Mandatory = $true)]
  [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
  [string]$PlanPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeDirectory = Join-Path $repositoryRoot "terraform\runtime"
$resolvedPlanPath = (Resolve-Path -LiteralPath $PlanPath).Path
$planJson = & terraform "-chdir=$runtimeDirectory" show -json $resolvedPlanPath
if ($LASTEXITCODE -ne 0) {
  throw "Unable to read the saved temporary-capacity plan."
}

$plan = $planJson | ConvertFrom-Json
$changes = @($plan.resource_changes)
$clusterChange = $changes | Where-Object { $_.address -eq "google_container_cluster.staging" }

if (($clusterChange | Measure-Object).Count -ne 1 -or $clusterChange.change.actions -ne @("update")) {
  throw "The plan must contain one in-place staging cluster update."
}

foreach ($change in $changes) {
  if ($change.address -eq "google_container_cluster.staging") {
    continue
  }
  if ($change.address -notmatch '^google_project_service\.runtime\[' -or $change.change.actions -ne @("no-op")) {
    throw "Unexpected temporary-capacity plan change: $($change.address)"
  }
}

$beforeType = $clusterChange.change.before.node_config[0].machine_type
$afterType = $clusterChange.change.after.node_config[0].machine_type
if ($beforeType -ne "e2-medium" -or $afterType -ne "e2-standard-4") {
  throw "The plan must change only from e2-medium to e2-standard-4."
}

Write-Output "Staging temporary-capacity plan guardrails passed."
