{{/*
Common helpers.
*/}}

{{- define "dograh.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dograh.fullname" -}}
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

{{- define "dograh.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "dograh.labels" -}}
helm.sh/chart: {{ include "dograh.chart" . }}
{{ include "dograh.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "dograh.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dograh.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "dograh.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "dograh.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Component-specific names.
*/}}
{{- define "dograh.web.fullname" -}}{{ include "dograh.fullname" . }}-web{{- end }}
{{- define "dograh.arqWorker.fullname" -}}{{ include "dograh.fullname" . }}-arq-worker{{- end }}
{{- define "dograh.ariManager.fullname" -}}{{ include "dograh.fullname" . }}-ari-manager{{- end }}
{{- define "dograh.campaignOrchestrator.fullname" -}}{{ include "dograh.fullname" . }}-campaign-orchestrator{{- end }}
{{- define "dograh.ui.fullname" -}}{{ include "dograh.fullname" . }}-ui{{- end }}
{{- define "dograh.coturn.fullname" -}}{{ include "dograh.fullname" . }}-coturn{{- end }}
{{- define "dograh.migrate.fullname" -}}{{ include "dograh.fullname" . }}-migrate{{- end }}

{{- define "dograh.configMapName" -}}{{ include "dograh.fullname" . }}-config{{- end }}
{{- define "dograh.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- include "dograh.fullname" . }}-secret
{{- end -}}
{{- end }}

{{/*
Image reference.
*/}}
{{- define "dograh.image" -}}
{{- $registry := .Values.image.registry | default "docker.io" -}}
{{- printf "%s/%s:%s" $registry .Values.image.repository .Values.image.tag -}}
{{- end }}

{{- define "dograh.ui.image" -}}
{{- $registry := .Values.ui.image.registry | default "docker.io" -}}
{{- printf "%s/%s:%s" $registry .Values.ui.image.repository .Values.ui.image.tag -}}
{{- end }}

{{- define "dograh.coturn.image" -}}
{{- $registry := .Values.coturn.image.registry | default "docker.io" -}}
{{- printf "%s/%s:%s" $registry .Values.coturn.image.repository .Values.coturn.image.tag -}}
{{- end }}

{{/*
Subchart enabling — flips top-level chart-dependency `enabled` flags from mode.
Called from each template via `include "dograh.deps.resolved" .` (no-op output).
*/}}
{{- define "dograh.deps.resolved" -}}
{{- /* compute whether internal deps are enabled */ -}}
{{- end }}

{{/*
In-cluster service references for internal deps.
*/}}
{{- define "dograh.postgresHost" -}}{{ .Release.Name }}-postgresql{{- end }}
{{- define "dograh.redisHost" -}}{{ .Release.Name }}-redisinternal-master{{- end }}
{{- define "dograh.minioHost" -}}{{ .Release.Name }}-minio{{- end }}

{{/*
Default DATABASE_URL when database.mode=internal.
Bitnami Postgres exposes the password as <release>-postgresql secret key
`postgres-password` or `password`. The chart pulls it via envFrom on the
generated secret; for clarity we still need the URL string. Auth username
defaults to `dograh` (see values.postgresql.auth.username).
*/}}
{{- define "dograh.databaseUrl" -}}
{{- if eq .Values.database.mode "internal" -}}
postgresql+asyncpg://{{ .Values.postgresql.auth.username }}:$(POSTGRES_PASSWORD)@{{ include "dograh.postgresHost" . }}:5432/{{ .Values.postgresql.auth.database }}
{{- else -}}
$(DATABASE_URL)
{{- end -}}
{{- end }}

{{- define "dograh.redisUrl" -}}
{{- if eq .Values.redis.mode "internal" -}}
redis://:$(REDIS_PASSWORD)@{{ include "dograh.redisHost" . }}:6379
{{- else -}}
$(REDIS_URL)
{{- end -}}
{{- end }}

{{/*
Common env block for backend workloads (web, arq, singletons, migrate).
References the ConfigMap + Secret via envFrom. DATABASE_URL and REDIS_URL
are added inline because they may need composition from subchart secrets.
*/}}
{{- define "dograh.backendEnvFrom" -}}
- configMapRef:
    name: {{ include "dograh.configMapName" . }}
- secretRef:
    name: {{ include "dograh.secretName" . }}
{{- if eq .Values.database.mode "internal" }}
- secretRef:
    name: {{ .Release.Name }}-postgresql
    optional: true
{{- end }}
{{- if eq .Values.redis.mode "internal" }}
- secretRef:
    name: {{ .Release.Name }}-redisinternal
    optional: true
{{- end }}
{{- end }}

{{/*
Volume mounts for the shared-tmp PVC when enabled.
*/}}
{{- define "dograh.sharedTmpVolumeMounts" -}}
{{- if .Values.sharedTmp.enabled }}
- name: shared-tmp
  mountPath: {{ .Values.sharedTmp.mountPath }}
{{- end }}
{{- end }}

{{- define "dograh.sharedTmpVolumes" -}}
{{- if .Values.sharedTmp.enabled }}
- name: shared-tmp
  persistentVolumeClaim:
    claimName: {{ include "dograh.fullname" . }}-shared-tmp
{{- end }}
{{- end }}
