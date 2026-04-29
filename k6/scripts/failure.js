import http from "k6/http";
import { check, sleep } from "k6";

const targetUrl = (__ENV.TARGET_URL || "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local").replace(/\/+$/, "");
const failurePath = __ENV.FAILURE_PATH || "/break";
const sleepSeconds = Number(__ENV.SLEEP_SECONDS || 0.1);
const scenarioLabel = __ENV.SCENARIO_LABEL || "failure-unknown";

export const options = {
  vus: Number(__ENV.VUS || 20),
  duration: __ENV.DURATION || "5m",
  tags: {
    scenario: scenarioLabel,
  },
};

export default function () {
  const res = http.get(`${targetUrl}${failurePath}`, { tags: { scenario: scenarioLabel } });
  check(res, {
    "failure: response received": (r) => r.status > 0,
    "failure: generated 5xx": (r) => r.status >= 500,
  });
  sleep(sleepSeconds);
}
