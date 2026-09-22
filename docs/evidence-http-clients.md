# Evidence HTTP Client Contracts

`evidence_gateway.runtime.http_clients` implements three fixed-operation
HTTPS clients. Constructors require an injected `HTTPExecutor`; there is no
default socket executor, credential discovery, or network call on import.
The existing fake providers and runtime interfaces remain compatible.

## Operations

| Client | HTTPS destination | Method and route | Response limit |
| --- | --- | --- | --- |
| `KubernetesTokenReviewClient` | `kubernetes.default.svc:443` | `POST /apis/authentication.k8s.io/v1/tokenreviews` | 16 KiB |
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
or body buffering. Only TLS connection creation is exposed. No live executor,
Kubernetes source backend, deployment configuration, or resources are included.

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
