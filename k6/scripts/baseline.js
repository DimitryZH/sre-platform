import http from "k6/http";
import { check, sleep } from "k6";

const targetUrl = (__ENV.TARGET_URL || "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local").replace(/\/+$/, "");
const requestPath = __ENV.REQUEST_PATH || "/";
const sleepSeconds = Number(__ENV.SLEEP_SECONDS || 0.1);

export const options = {
  vus: Number(__ENV.VUS || 20),
  duration: __ENV.DURATION || "30m",
  tags: {
    scenario: __ENV.SCENARIO_LABEL || "baseline",
  },
};

export default function () {
  const res = http.get(`${targetUrl}${requestPath}`);
  check(res, {
    "baseline: response received": (r) => r.status > 0,
    "baseline: non-5xx": (r) => r.status < 500,
  });
  sleep(sleepSeconds);
}
