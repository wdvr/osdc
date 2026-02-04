{{/*
Expand the name of the chart.
*/}}
{{- define "gpu-dev-server.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "gpu-dev-server.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "gpu-dev-server.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "gpu-dev-server.labels" -}}
helm.sh/chart: {{ include "gpu-dev-server.chart" . }}
{{ include "gpu-dev-server.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "gpu-dev-server.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gpu-dev-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Controlplane namespace
*/}}
{{- define "gpu-dev-server.controlplaneNamespace" -}}
{{- .Values.namespaces.controlplane }}
{{- end }}

{{/*
Workloads namespace
*/}}
{{- define "gpu-dev-server.workloadsNamespace" -}}
{{- .Values.namespaces.workloads }}
{{- end }}

{{/*
PostgreSQL primary service name
*/}}
{{- define "gpu-dev-server.postgresPrimaryService" -}}
postgres-primary
{{- end }}

{{/*
PostgreSQL replica service name
*/}}
{{- define "gpu-dev-server.postgresReplicaService" -}}
postgres-replica
{{- end }}

{{/*
PostgreSQL connection host (primary)
*/}}
{{- define "gpu-dev-server.postgresHost" -}}
{{- printf "%s.%s.svc.cluster.local" (include "gpu-dev-server.postgresPrimaryService" .) (include "gpu-dev-server.controlplaneNamespace" .) }}
{{- end }}

{{/*
PostgreSQL credentials secret name
*/}}
{{- define "gpu-dev-server.postgresSecretName" -}}
{{- if .Values.postgres.auth.existingSecret }}
{{- .Values.postgres.auth.existingSecret }}
{{- else }}
postgres-credentials
{{- end }}
{{- end }}

{{/*
Registry native DNS name
*/}}
{{- define "gpu-dev-server.registryNativeDns" -}}
{{- printf "registry-native.%s.svc.cluster.local:5000" (include "gpu-dev-server.controlplaneNamespace" .) }}
{{- end }}

{{/*
Registry ghcr DNS name
*/}}
{{- define "gpu-dev-server.registryGhcrDns" -}}
{{- printf "registry-ghcr.%s.svc.cluster.local:5000" (include "gpu-dev-server.controlplaneNamespace" .) }}
{{- end }}

{{/*
Is AWS cloud provider
*/}}
{{- define "gpu-dev-server.isAws" -}}
{{- eq .Values.cloudProvider.name "aws" }}
{{- end }}

{{/*
Is GCP cloud provider
*/}}
{{- define "gpu-dev-server.isGcp" -}}
{{- eq .Values.cloudProvider.name "gcp" }}
{{- end }}

{{/*
Storage class name
*/}}
{{- define "gpu-dev-server.storageClass" -}}
{{- .Values.storage.class }}
{{- end }}
