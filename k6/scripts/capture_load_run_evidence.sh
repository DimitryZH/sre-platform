#!/usr/bin/env bash
set -euo pipefail

SCENARIO="${1:-}"
JOB_NAME="${2:-}"
RUN_ID_INPUT="${3:-}"
NAMESPACE="${LOAD_NAMESPACE:-online-shop-dev}"

if [[ -z "${SCENARIO}" || -z "${JOB_NAME}" ]]; then
  echo "Usage: scripts/capture_load_run_evidence.sh <scenario> <job-name> <run-id>"
  echo "Example: scripts/capture_load_run_evidence.sh baseline online-shop-load-baseline baseline-20260429-1200"
  exit 1
fi

if [[ -n "${RUN_ID_INPUT}" ]]; then
  RUN_ID="${RUN_ID_INPUT}"
else
  RUN_ID="${SCENARIO}-$(date -u +%Y%m%d-%H%M%S)"
fi

OUT_DIR="docs/evidence/load-runs/${RUN_ID}"
mkdir -p "${OUT_DIR}"

START_TIME_UTC="$(kubectl -n "${NAMESPACE}" get job "${JOB_NAME}" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || true)"
if [[ -z "${START_TIME_UTC}" ]]; then
  START_TIME_UTC="UNKNOWN"
fi

EXPECTED_BEHAVIOR="Scenario behavior to be validated by operator."
case "${SCENARIO}" in
  baseline)
    EXPECTED_BEHAVIOR="Steady healthy traffic to / with no failure injection."
    ;;
  failure-10)
    EXPECTED_BEHAVIOR="Failure traffic to /break during rollout 10% gate validation."
    ;;
  failure-50)
    EXPECTED_BEHAVIOR="Failure traffic to /break during rollout 50% gate validation."
    ;;
esac

cat > "${OUT_DIR}/summary.md" <<EOF
# Load Run Summary

- run_id: ${RUN_ID}
- scenario: ${SCENARIO}
- namespace: ${NAMESPACE}
- start_time_utc: ${START_TIME_UTC}
- expected_behavior: ${EXPECTED_BEHAVIOR}
- pass_fail: PENDING
- notes: Fill after reviewing k6 logs, rollout status, and analysis runs.
EOF

{
  echo "\$ kubectl -n ${NAMESPACE} logs job/${JOB_NAME}"
  kubectl -n "${NAMESPACE}" logs "job/${JOB_NAME}" 2>&1 || true
} > "${OUT_DIR}/k6.txt"

{
  echo "\$ kubectl -n ${NAMESPACE} get job ${JOB_NAME} -o wide"
  kubectl -n "${NAMESPACE}" get job "${JOB_NAME}" -o wide 2>&1 || true
  echo
  echo "\$ kubectl -n ${NAMESPACE} describe job ${JOB_NAME}"
  kubectl -n "${NAMESPACE}" describe job "${JOB_NAME}" 2>&1 || true
} > "${OUT_DIR}/job.txt"

{
  echo "\$ kubectl -n ${NAMESPACE} get pods -l app.kubernetes.io/name=online-shop-k6 -o wide"
  kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=online-shop-k6 -o wide 2>&1 || true
} > "${OUT_DIR}/pods.txt"

{
  echo "\$ kubectl -n ${NAMESPACE} get rollout frontend"
  kubectl -n "${NAMESPACE}" get rollout frontend 2>&1 || true
  echo
  echo "\$ kubectl -n ${NAMESPACE} get rollout frontend -o yaml"
  kubectl -n "${NAMESPACE}" get rollout frontend -o yaml 2>&1 || true
} > "${OUT_DIR}/rollout.txt"

{
  echo "\$ kubectl -n ${NAMESPACE} get analysisrun --sort-by=.metadata.creationTimestamp"
  kubectl -n "${NAMESPACE}" get analysisrun --sort-by=.metadata.creationTimestamp 2>&1 || true
} > "${OUT_DIR}/analysisruns.txt"

echo "Evidence captured at ${OUT_DIR}"
