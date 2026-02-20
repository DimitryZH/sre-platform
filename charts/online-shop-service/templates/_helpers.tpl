{{/*
Create a default fully qualified appVersion.
We truncate the appVersion to 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
*/}}
{{- define "online-shop-service.appVersion" -}}
{{- if .Values.image.tag -}}
{{- .Values.image.tag -}}
{{- else -}}
{{- .Chart.AppVersion | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
