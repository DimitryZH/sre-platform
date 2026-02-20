{{/*
Create a default fully qualified appVersion.
We truncate the appVersion to 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
*/}}
{{- define "platform.appVersion" -}}
{{- if .Values.image.tag -}}
{{- .Values.image.tag -}}
{{- else -}}
{{- .Chart.AppVersion | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
