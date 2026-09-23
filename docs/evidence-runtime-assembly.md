# Evidence Runtime Assembly

The repository packages one Python image with two explicit roles: `gateway`
and `source`. The image does not select a role implicitly. Both roles accept
only a listen IP/port and file paths for the internal CA, Kubernetes API CA,
role certificate/private key, projected Kubernetes token, and, for the
Gateway role only, a separate server certificate/private key. Backend URLs,
resources, selectors, queries, repository paths, and Git references are not
configuration inputs.

The Gateway exposes the existing single staging frontend operation over HTTPS
only. Its server certificate must contain exactly the fixed Gateway Service
DNS SAN, `serverAuth` EKU, and a current validity interval. The listener does
not request a validation-client certificate: caller identity remains derived
only through the existing TokenReview authentication. The Gateway also uses
bounded SQLite replay/rate/audit state, policy core, and a concrete
`SourceHTTPClient`. The assembled policy
permits only `kubernetes_state` and `deployment_revision`; Events, logs, pod
status, Prometheus, GitOps Contents, and all other evidence kinds fail before
source transport calls. SQLite uses the fixed
`/var/lib/evidence-gateway/runtime.db` path. This repository does not provide
a PVC or claim restart durability without an approved persistent mount.

The private Source Service exposes only:

- `GET /v1/evidence/staging/frontend/state`;
- `GET /v1/evidence/staging/frontend/deployment-revision`.

Its TLS listener requires a client certificate signed by the configured
internal CA. The certificate is normalized from the completed TLS connection
and must have exactly the Gateway URI SAN, `clientAuth` EKU, and a current
validity interval. There is no plaintext listener or identity-header path.
The Gateway client verifies the configured CA, exact source DNS and URI SANs,
`serverAuth` EKU, validity, and hostname before sending HTTP.
The Gateway server TLS certificate/key and Gateway-to-Source client mTLS
certificate/key are distinct required configuration paths and cannot be
reused implicitly. Both listeners are TLS-wrapped before serving requests;
there is no plaintext listener or fallback for either role.

The Source uses a separate projected token and Kubernetes API CA. It can issue
only three fixed GET requests: `Rollout/frontend` and
`Ingress/online-shop-frontend` in `online-shop-stage`, and
`Application/online-shop-stage` in `argocd`. Response streams are bounded
before JSON materialization. Raw objects are checked and projected to the
already accepted source schemas. The stage ingress's exact rendered API path
is validated and normalized to `/stage`. No list, watch, log, Event, pod,
AnalysisRun, generic resource, or caller-supplied request is implemented.

The concrete HTTPS executor uses direct TLS connections only. It has no
environment proxy discovery, redirect, retry, or plaintext fallback. Header
and body handling are bounded, and transport/backend failures are mapped to
safe unavailable responses without backend details.

The `Dockerfile` uses the immutable multi-platform digest for the official
`python:3.13.7-slim-bookworm` base and runs as UID/GID 65532. It contains no
registry destination, runtime image digest, credentials, or deployment
resources. The deny-by-default `.dockerignore` sends only the Dockerfile,
pinned runtime requirements, and `evidence_gateway` package to a future build
context. No Kubernetes manifests, ServiceAccounts, RBAC, Secrets,
NetworkPolicies, Services, PVCs, or cloud resources are included. Container
build, image publication, private network enforcement, operator-managed
certificate issuance, durable volume mounting, deployment, and live
validation remain separate operator actions.
