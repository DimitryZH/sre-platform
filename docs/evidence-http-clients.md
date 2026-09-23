# Evidence HTTP Client Contracts

`evidence_gateway.runtime.http_clients` implements four fixed-operation
HTTPS clients. Constructors require an injected `HTTPExecutor`; there is no
default socket executor, credential discovery, or network call on import.
The existing fake providers and runtime interfaces remain compatible.

## Operations

| Client | HTTPS destination | Method and route | Response limit |
| --- | --- | --- | --- |
| `KubernetesTokenReviewClient` | `kubernetes.default.svc:443` | `POST /apis/authentication.k8s.io/v1/tokenreviews` | 16 KiB |
| `KubernetesObjectHTTPClient` | `kubernetes.default.svc:443` | `GET /apis/argoproj.io/v1alpha1/namespaces/online-shop-stage/rollouts/frontend` | 64 KiB |
| `KubernetesObjectHTTPClient` | `kubernetes.default.svc:443` | `GET /apis/networking.k8s.io/v1/namespaces/online-shop-stage/ingresses/online-shop-frontend` | 32 KiB |
| `KubernetesObjectHTTPClient` | `kubernetes.default.svc:443` | `GET /apis/argoproj.io/v1alpha1/namespaces/argocd/applications/online-shop-stage` | 32 KiB |
| `SourceHTTPClient` | `evidence-source-stage.evidence-gateway-stage.svc:8443` | `GET /v1/evidence/staging/frontend/state` | 16 KiB |
| `SourceHTTPClient` | same source service | `GET /v1/evidence/staging/frontend/deployment-revision` | 4 KiB |
| `GitHubEgressHTTPClient` | `github-egress.evidence-gateway-stage.svc:8443` | `GET /repos/DimitryZH/sre-platform/contents/{allowlisted-path}?ref={immutable-sha}` | at most 16 KiB |

The GitHub client accepts only the existing seven GitOps path IDs, a lowercase
40-character commit SHA, and a positive response limit no greater than 16 KiB.
It maps IDs to repository paths internally. It never connects directly to
GitHub and does not forward credentials to the egress service. The enclosing
runtime GitOps provider binds these reads to the resolved deployed revision.
Decoded file content remains limited to 4096 bytes. Additional Contents API
fields, including returned URLs, are discarded and never followed.

TokenReview constructs `authentication.k8s.io/v1`, `kind: TokenReview`, with
only the submitted token and the fixed evidence audience in `spec`. The
Authorization header uses a separate, injected runtime token reader. That
reader is responsible for supplying the projected Kubernetes API token with
the API-server audience; the incoming evidence token is never used to
authenticate to the Kubernetes API. Tokens are bounded to 8192 ASCII token
characters; malformed tokens and unsupported audiences fail before opening a
connection. No JWT claims supplied by the caller establish identity.

A successful response requires an actual boolean `status.authenticated`,
the exact approved Kubernetes username, and exactly the evidence audience.
Authentication denial yields a non-authenticated result. Malformed replies
and transport failures raise the safe `ProviderError`, which the runtime
authentication boundary treats as unauthorized.

## TLS And Executor Boundary

`TLSConfiguration` loads the operator-supplied CA file into an explicit
`PROTOCOL_TLS_CLIENT` context. It requires certificate verification, hostname
verification with SANs rather than common-name fallback, and TLS 1.2 or newer.
For source and egress, it loads the supplied client certificate/key pair and
checks the normalized client identity against the fixed gateway URI SAN.
No CA, certificate, private key, or runtime token is bundled in the repository.

The executor must complete the TLS handshake using this context and the exact
supplied SNI hostname before returning a connection. Its normalized peer
certificate must describe that connection's verified chain, SANs, validity
interval and EKU. Source and egress peers must match their fixed DNS and URI
SANs and server EKU before any HTTP request is sent. Kubernetes peers must
include the exact API-server DNS SAN and server EKU. The client identity facts
are trusted configuration and must describe the loaded certificate/key pair.

The executor is the trusted I/O boundary. Its contract prohibits environment
proxy discovery, redirects, retries, plaintext fallback, and unbounded header
or body buffering. Only TLS connection creation is exposed. The runtime
assembly supplies a direct stdlib TLS executor and exact-object source
backends; all clients remain injectable for offline tests. No deployment
configuration or resources are included.

## Bounded Responses

One exchange has a five-second deadline. The remaining time is passed to
connection establishment, request execution, and each stream read. The
executor must honor those timeouts, including handshake and header reads.
Every body read requests at most 4096 bytes and stops at the endpoint limit
plus one detection byte. Missing or dishonest `Content-Length` cannot remove
this bound. A declared length must also match the actual bytes. Compressed
responses, oversized chunks and non-JSON content types are rejected.

Only HTTP 201 for TokenReview and HTTP 200 for source/egress are accepted.
Error bodies, including GitHub 403/429 responses, are never read or returned.
Every acquired connection is closed on success or failure. JSON parsing
rejects duplicate keys, invalid UTF-8 and nonstandard numeric constants.
Backend exceptions become a fixed `ProviderError` without backend details.

Source state is the exact normalized `workload`, `rollout`, `ingress` schema
already used by runtime providers. The client checks fixed names and namespace,
integer replica/step values, bounded string fields, at most eight conditions,
the exact `/stage` path, and an empty AnalysisRun list. Extra fields and raw
Kubernetes objects fail closed. Deployment revision accepts only application,
immutable revision, sync status and health status. Each response is projected
before returning it to the runtime's existing evidence sanitization boundary.
Events, logs, pod status and Prometheus have no client operations here.

Offline tests inject connections and certificate loading, prohibit sockets,
and exercise exact requests, identity failures, malformed responses, streamed
limits, timeouts, safe error mapping and complete runtime collection.

## Private Source Service

`evidence_gateway.runtime.source_service.PrivateSourceService` dispatches only
`GET /v1/evidence/staging/frontend/state` and
`GET /v1/evidence/staging/frontend/deployment-revision`. It uses injected
`KubernetesEvidenceAdapter` and `ArgoCDGitOpsAdapter` instances. It does not
open a listener, create a Kubernetes API client, or discover credentials.
The separate runtime assembly wraps this dispatcher in the mTLS listener and
injects the exact-object Kubernetes and Argo CD clients described above.

The `handle` boundary accepts peer facts exclusively through its separate
trusted-transport argument. Those facts must describe the verified TLS peer
on the same connection, not an HTTP header, body, or caller assertion.
The existing mTLS validator requires configured-CA trust, exactly the gateway
URI SAN `spiffe://evidence-gateway-stage/gateway`, exactly `clientAuth` EKU,
and a currently valid, timezone-aware certificate interval at full precision.
Missing, malformed, untrusted, expired, or wrong-identity peers receive a
fixed 401 response without adapter calls. The dispatcher itself does not
perform a TLS handshake or establish certificate trust.

The transport must preserve the original request target, reject duplicate
headers before constructing the header mapping, bound header parsing, and
reject nonempty request bodies without unbounded buffering. The dispatcher
requires an empty bytes body and exact route matching:
it does not decode, normalize or follow supplied paths. Query strings,
fragments, alternate methods, and all other routes are rejected. Only a fixed
source Host (optionally with port 8443), `Accept: application/json`,
`Accept-Encoding: identity`, and `Content-Length: 0` are permitted; Host is
required and other headers are optional. Duplicate case-insensitive header
names and all other headers, including authentication and forwarded identity
headers, are rejected. Invalid requests cause zero adapter/backend calls.

For state, the adapter reads only `Rollout/frontend` and
`Ingress/online-shop-frontend` in `online-shop-stage`, using a fresh
server-owned target. Workload and rollout projections come from that same
normalized Rollout record, never a Deployment. Revision reads resolve only
`Application/online-shop-stage` on each request and return its lowercase
40-character immutable SHA, sync status and health status. No Git file read
is performed by this service.

Both sides use the same strict projections in `runtime.source_contract`.
State is limited to the documented workload, rollout and ingress fields,
at most eight conditions, bounded strings and integers, the exact `/stage`
path and an empty AnalysisRun list. Nonempty AnalysisRun data is rejected;
backends must supply that empty field without querying AnalysisRuns. No Events,
logs, pod status, Prometheus or AnalysisRun operations are exposed or called.
Raw or extra fields in the normalized source schema fail closed. Metadata
already discarded by the adapters is not returned. Backend output is checked
after the necessary read; malformed output cannot be detected before that read.

Successful JSON responses are bounded to 16 KiB for state and 4096 bytes for
revision, measured after canonical serialization. Backend exceptions,
out-of-scope data, malformed projections and oversized responses become a
fixed 503 `backend_unavailable` response, with no backend details or raw data.
Offline tests exercise the dispatcher through `SourceHTTPClient`, both injected
adapters, and runtime evidence collection, with sockets prohibited.
