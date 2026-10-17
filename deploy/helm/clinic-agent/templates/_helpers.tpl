{{- define "clinic-agent.name" -}}clinic-agent{{- end -}}

{{- define "clinic-agent.fullname" -}}
{{- if contains "clinic-agent" .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-clinic-agent" .Release.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "clinic-agent.imageTag" -}}
{{- required "image.tag is required: deploy a specific git commit SHA, e.g. --set image.tag=$(git rev-parse --short HEAD)" .Values.image.tag -}}
{{- end -}}

{{- define "clinic-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "clinic-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "clinic-agent.labels" -}}
{{ include "clinic-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ include "clinic-agent.imageTag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
