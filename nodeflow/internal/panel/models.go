package panel

import (
	"time"

	"github.com/nodeflow/nodeflow/internal/agent"
)

const (
	MaxManagedConfigBytes  = 512 << 10
	MaxReportDetailsBytes  = 16 << 10
	MaxRouteSNIs           = 64
	MaxCustomFragmentBytes = 8 << 10
	MaxUnixSocketPathBytes = 107
)

type Node struct {
	ID        string     `json:"id"`
	Name      string     `json:"name"`
	Address   string     `json:"address"`
	Status    string     `json:"status"`
	Metadata  any        `json:"metadata"`
	LastSeen  *time.Time `json:"last_seen_at,omitempty"`
	CreatedAt time.Time  `json:"created_at"`
	UpdatedAt time.Time  `json:"updated_at"`
	SortOrder int64      `json:"sort_order,omitempty"`
}

type Route struct {
	ID                  string    `json:"id"`
	NodeID              string    `json:"node_id"`
	Name                string    `json:"name"`
	Version             int64     `json:"version"`
	ListenerIP          string    `json:"listener_ip"`
	ListenerPort        int       `json:"listener_port"`
	MatchMode           string    `json:"match_mode"`
	SNIs                []string  `json:"snis"`
	Fallback            bool      `json:"fallback"`
	Hostname            string    `json:"hostname"`
	TargetType          string    `json:"target_type"`
	TargetHost          string    `json:"target_host"`
	TargetPort          int       `json:"target_port"`
	UnixSocketPath      string    `json:"unix_socket_path"`
	HealthCheck         bool      `json:"health_check"`
	ProxyProtocol       string    `json:"proxy_protocol"`
	QuotaBytes          *int64    `json:"quota_bytes"`
	QuotaAction         string    `json:"quota_action"`
	QuotaPeriod         string    `json:"quota_period"`
	Enabled             bool      `json:"enabled"`
	Deployed            bool      `json:"deployed"`
	DeploymentState     string    `json:"deployment_state"`
	DeploymentError     string    `json:"deployment_error,omitempty"`
	DesiredRevision     *int64    `json:"desired_revision,omitempty"`
	AppliedRevision     *int64    `json:"applied_revision,omitempty"`
	DesiredFingerprint  string    `json:"desired_fingerprint"`
	DeployedFingerprint string    `json:"deployed_fingerprint"`
	DeletePending       bool      `json:"delete_pending"`
	CustomFragment      string    `json:"custom_fragment"`
	CreatedAt           time.Time `json:"created_at"`
	UpdatedAt           time.Time `json:"updated_at"`
	SortOrder           int64     `json:"sort_order,omitempty"`
}

// RouteDeleteResult distinguishes an immediate draft deletion from an active
// route removal that must first be confirmed by the Node Agent.
type RouteDeleteResult struct {
	Route   Route
	Pending bool
}

// RouteSpec is the validated, canonical route representation accepted by Store.
// Hostname is intentionally retained as the first SNI for legacy API clients.
type RouteSpec struct {
	ExpectedVersion *int64
	Name            string
	ListenerIP      string
	ListenerPort    int
	MatchMode       string
	SNIs            []string
	Fallback        bool
	Hostname        string
	TargetType      string
	TargetHost      string
	TargetPort      int
	UnixSocketPath  string
	HealthCheck     bool
	ProxyProtocol   string
	QuotaBytes      *int64
	QuotaAction     string
	QuotaPeriod     string
	Enabled         bool
	CustomFragment  string
}

type EnrollmentToken struct {
	ID        string    `json:"id"`
	NodeID    string    `json:"node_id"`
	Prefix    string    `json:"prefix"`
	ExpiresAt time.Time `json:"expires_at"`
	CreatedAt time.Time `json:"created_at"`
}

// AgentCredentialIdentity is derived only from the verified TLS leaf. It is
// never populated from JSON supplied by an Agent.
type AgentCredentialIdentity struct {
	NodeID              string
	CertificateSHA256   string
	CertificateSerial   string
	CertificateNotAfter time.Time
}

type CredentialRenewalRequest struct {
	RenewalID       string
	CSRHash         string
	CSRDER          []byte
	NextTokenHash   string
	NextTokenPrefix string
}

type CredentialRenewalCandidate struct {
	CredentialRenewalRequest
	CertificateSHA256   string
	CertificateSerial   string
	CertificateDER      []byte
	CertificateNotAfter time.Time
	ConfirmBy           time.Time
}

type CredentialRenewalRecord struct {
	ID                  string
	NodeID              string
	RenewalID           string
	PredecessorID       string
	CSRHash             string
	CSRDER              []byte
	NextTokenHash       string
	NextTokenPrefix     string
	CertificateSHA256   string
	CertificateSerial   string
	CertificateDER      []byte
	CertificateNotAfter time.Time
	ConfirmBy           time.Time
	ActivatedAt         *time.Time
	RevokedAt           *time.Time
	CreatedAt           time.Time
}

type AuditEvent struct {
	ActorType    string         `json:"actor_type"`
	ActorID      string         `json:"actor_id,omitempty"`
	Action       string         `json:"action"`
	ResourceType string         `json:"resource_type"`
	ResourceID   string         `json:"resource_id,omitempty"`
	Details      map[string]any `json:"details"`
	SourceIP     string         `json:"source_ip,omitempty"`
}

type AuditEntry struct {
	ID           int64          `json:"id"`
	ActorType    string         `json:"actor_type"`
	ActorID      string         `json:"actor_id,omitempty"`
	Action       string         `json:"action"`
	ResourceType string         `json:"resource_type"`
	ResourceID   string         `json:"resource_id,omitempty"`
	Details      map[string]any `json:"details"`
	SourceIP     string         `json:"source_ip,omitempty"`
	CreatedAt    time.Time      `json:"created_at"`
}

// PanelSettings contains the mutable, persisted operator preferences and
// security policy. Listener addresses and public URLs remain runtime-only
// Config values and are intentionally not part of this model.
type PanelSettings struct {
	Theme                    string    `json:"theme"`
	Accent                   string    `json:"accent"`
	InactivityTimeoutMinutes int       `json:"session_timeout_minutes"`
	MaxSessions              int       `json:"max_sessions"`
	AuditRetentionDays       int       `json:"audit_retention_days"`
	UpdatedAt                time.Time `json:"updated_at"`
}

type Heartbeat struct {
	Version                  string                  `json:"version"`
	Status                   string                  `json:"status"`
	Metrics                  map[string]any          `json:"metrics"`
	RoutesOK                 *bool                   `json:"routes_ok,omitempty"`
	ActualRevision           *int64                  `json:"actual_revision,omitempty"`
	ConfigSHA256             string                  `json:"config_sha256,omitempty"`
	TrafficInstanceID        string                  `json:"traffic_instance_id,omitempty"`
	TrafficInstanceStartedAt *time.Time              `json:"traffic_instance_started_at,omitempty"`
	TrafficSampleSeq         *int64                  `json:"traffic_sample_seq,omitempty"`
	HAProxyServiceState      string                  `json:"haproxy_service_state,omitempty"`
	HAProxyControlGeneration *int64                  `json:"haproxy_control_generation,omitempty"`
	HAProxyControlError      string                  `json:"haproxy_control_error,omitempty"`
	MTLSNodeID               string                  `json:"-"`
	Credential               AgentCredentialIdentity `json:"-"`
}

type ConfigAssignment struct {
	Revision int64  `json:"revision"`
	Config   string `json:"config"`
	SHA256   string `json:"sha256"`
}

type QuotaBackendPolicy struct {
	RouteID     string    `json:"route_id"`
	Backend     string    `json:"backend"`
	Server      string    `json:"server"`
	Action      string    `json:"action"`
	Block       bool      `json:"block"`
	UsedBytes   int64     `json:"used_bytes"`
	LimitBytes  *int64    `json:"limit_bytes,omitempty"`
	QuotaPeriod string    `json:"quota_period"`
	WindowStart time.Time `json:"window_start"`
	WindowEnd   time.Time `json:"window_end"`
}

type QuotaAssignment struct {
	Month    string               `json:"month"`
	Policies []QuotaBackendPolicy `json:"policies"`
}

type FirewallAssignment struct {
	Mode               string `json:"mode"`
	TCPPorts           []int  `json:"tcp_ports"`
	DesiredTCPPorts    []int  `json:"desired_tcp_ports,omitempty"`
	Transition         bool   `json:"transition,omitempty"`
	ActivePlanComplete bool   `json:"active_plan_complete,omitempty"`
}

type HAProxyServiceAssignment struct {
	Generation int64 `json:"generation"`
	Enabled    bool  `json:"enabled"`
}

type NodeHAProxyControl struct {
	NodeID           string     `json:"node_id"`
	Supported        bool       `json:"supported"`
	DesiredEnabled   bool       `json:"desired_enabled"`
	Generation       int64      `json:"generation"`
	ActualEnabled    *bool      `json:"actual_enabled,omitempty"`
	ActiveState      string     `json:"active_state"`
	ReportGeneration int64      `json:"report_generation"`
	LastError        string     `json:"last_error,omitempty"`
	ReportedAt       *time.Time `json:"reported_at,omitempty"`
	UpdatedAt        time.Time  `json:"updated_at"`
}

type NodeFirewallPolicy struct {
	NodeID       string    `json:"node_id"`
	Mode         string    `json:"mode"`
	TCPPorts     []int     `json:"tcp_ports"`
	PlanComplete bool      `json:"plan_complete"`
	UpdatedAt    time.Time `json:"updated_at"`
}

type HeartbeatResult struct {
	Status             string                    `json:"status"`
	NodeID             string                    `json:"node_id"`
	Assignment         *ConfigAssignment         `json:"assignment,omitempty"`
	QuotaAssignment    *QuotaAssignment          `json:"quota_assignment,omitempty"`
	FirewallAssignment *FirewallAssignment       `json:"firewall_assignment,omitempty"`
	UpdateAssignment   *agent.UpdateManifest     `json:"update_assignment,omitempty"`
	ServiceAssignment  *HAProxyServiceAssignment `json:"service_assignment,omitempty"`
}

type AgentRelease struct {
	ID           string    `json:"id"`
	Version      string    `json:"version"`
	OS           string    `json:"os"`
	Arch         string    `json:"arch"`
	SHA256       string    `json:"sha256"`
	SizeBytes    int64     `json:"size_bytes"`
	Sequence     int64     `json:"sequence"`
	Signature    string    `json:"signature"`
	ArtifactPath string    `json:"artifact_path,omitempty"`
	CreatedAt    time.Time `json:"created_at"`
}

type NodeAgentUpdateState struct {
	NodeID         string        `json:"node_id"`
	DesiredRelease *AgentRelease `json:"desired_release,omitempty"`
	ActualSequence int64         `json:"actual_sequence"`
	State          string        `json:"state"`
	LastError      string        `json:"last_error,omitempty"`
	LastReportAt   *time.Time    `json:"last_report_at,omitempty"`
	UpdatedAt      time.Time     `json:"updated_at"`
}

type NodeHeartbeat struct {
	AgentVersion             string         `json:"agent_version"`
	Status                   string         `json:"status"`
	Metrics                  map[string]any `json:"metrics"`
	RoutesOK                 *bool          `json:"routes_ok,omitempty"`
	TrafficInstanceID        string         `json:"traffic_instance_id,omitempty"`
	TrafficInstanceStartedAt *time.Time     `json:"traffic_instance_started_at,omitempty"`
	TrafficSampleSeq         *int64         `json:"traffic_sample_seq,omitempty"`
	ReceivedAt               time.Time      `json:"received_at"`
}

type NodeOperationalDetail struct {
	Node                        Node                `json:"node"`
	LatestHeartbeat             *NodeHeartbeat      `json:"latest_heartbeat,omitempty"`
	RoutesTotal                 int                 `json:"routes_total"`
	RoutesEnabled               int                 `json:"routes_enabled"`
	TrafficMonth                string              `json:"traffic_month"`
	TrafficBytesIn              int64               `json:"traffic_bytes_in"`
	TrafficBytesOut             int64               `json:"traffic_bytes_out"`
	TrafficUsed                 int64               `json:"traffic_used_bytes"`
	TrafficObserved             bool                `json:"traffic_observed"`
	TrafficDay                  string              `json:"traffic_day"`
	TrafficDayBytesIn           int64               `json:"traffic_day_bytes_in"`
	TrafficDayBytesOut          int64               `json:"traffic_day_bytes_out"`
	TrafficDayUsed              int64               `json:"traffic_day_used_bytes"`
	TrafficDayObserved          bool                `json:"traffic_day_observed"`
	TrafficDailyAverageBytesIn  *float64            `json:"traffic_daily_average_bytes_in,omitempty"`
	TrafficDailyAverageBytesOut *float64            `json:"traffic_daily_average_bytes_out,omitempty"`
	TrafficDailyAverageUsed     *float64            `json:"traffic_daily_average_used_bytes,omitempty"`
	TrafficDailyObservedDays    int                 `json:"traffic_daily_observed_days"`
	RXBitsPerSecond             *float64            `json:"rx_bits_per_second,omitempty"`
	TXBitsPerSecond             *float64            `json:"tx_bits_per_second,omitempty"`
	RateSampledAt               *time.Time          `json:"rate_sampled_at,omitempty"`
	MetricsSummary              *NodeMetricSummary  `json:"metrics_summary,omitempty"`
	CredentialPrefix            string              `json:"credential_prefix,omitempty"`
	CredentialExpiresAt         *time.Time          `json:"credential_expires_at,omitempty"`
	CredentialLastUsed          *time.Time          `json:"credential_last_used_at,omitempty"`
	HAProxyControl              *NodeHAProxyControl `json:"haproxy_control,omitempty"`
}

// DashboardOverview is the single read model used by the Nodes screen.  It is
// intentionally assembled in the store so the browser does not perform one
// operational/traffic request per node.
type DashboardOverview struct {
	Range          string                 `json:"range"`
	SelectedNodeID string                 `json:"selected_node_id,omitempty"`
	Nodes          []DashboardNode        `json:"nodes"`
	TrafficHistory TrafficHistory         `json:"traffic_history"`
	TopRoutes      []DashboardRoute       `json:"top_routes"`
	Totals         DashboardOverviewTotal `json:"totals"`
}

type DashboardNode struct {
	Node            Node           `json:"node"`
	LatestHeartbeat *NodeHeartbeat `json:"latest_heartbeat,omitempty"`
	RoutesTotal     int            `json:"routes_total"`
	RoutesEnabled   int            `json:"routes_enabled"`
	TrafficMonth    string         `json:"traffic_month"`
	TrafficBytesIn  int64          `json:"traffic_bytes_in"`
	TrafficBytesOut int64          `json:"traffic_bytes_out"`
	TrafficUsed     int64          `json:"traffic_used_bytes"`
	TrafficObserved bool           `json:"traffic_observed"`
	RXBitsPerSecond *float64       `json:"rx_bits_per_second"`
	TXBitsPerSecond *float64       `json:"tx_bits_per_second"`
	RateSampledAt   *time.Time     `json:"rate_sampled_at,omitempty"`
}

type DashboardRoute struct {
	RouteID         string   `json:"route_id"`
	NodeID          string   `json:"node_id"`
	NodeName        string   `json:"node_name"`
	Name            string   `json:"name"`
	ListenerIP      string   `json:"listener_ip"`
	ListenerPort    int      `json:"listener_port"`
	SNIs            []string `json:"snis"`
	Fallback        bool     `json:"fallback"`
	BytesIn         int64    `json:"bytes_in"`
	BytesOut        int64    `json:"bytes_out"`
	UsedBytes       int64    `json:"used_bytes"`
	RXBitsPerSecond float64  `json:"rx_bits_per_second"`
	TXBitsPerSecond float64  `json:"tx_bits_per_second"`
	BitsPerSecond   float64  `json:"bits_per_second"`
	SharePercent    float64  `json:"share_percent"`
}

// DashboardOverviewTotal exposes current rates only when every non-offline
// node has a rate sample matching its latest heartbeat.
type DashboardOverviewTotal struct {
	NodesTotal          int      `json:"nodes_total"`
	NodesOnline         int      `json:"nodes_online"`
	NodesDegraded       int      `json:"nodes_degraded"`
	NodesOffline        int      `json:"nodes_offline"`
	RoutesTotal         int      `json:"routes_total"`
	ConnectionsCurrent  uint64   `json:"connections_current"`
	RXBitsPerSecond     *float64 `json:"rx_bits_per_second"`
	TXBitsPerSecond     *float64 `json:"tx_bits_per_second"`
	CurrentRateComplete bool     `json:"current_rate_complete"`
	TrafficMonthBytes   int64    `json:"traffic_month_bytes"`
	BackendsHealthy     int      `json:"backends_healthy"`
	BackendsDegraded    int      `json:"backends_degraded"`
	BackendsUnavailable int      `json:"backends_unavailable"`
}

type MetricAverageDelta struct {
	Average      *float64   `json:"average,omitempty"`
	Delta        *float64   `json:"delta,omitempty"`
	SampleCount  int64      `json:"sample_count"`
	ObservedFrom *time.Time `json:"observed_from,omitempty"`
	ObservedTo   *time.Time `json:"observed_to,omitempty"`
}

type NodeMetricSummary struct {
	Range         string              `json:"range"`
	From          *time.Time          `json:"from,omitempty"`
	To            *time.Time          `json:"to,omitempty"`
	SampleCount   int64               `json:"sample_count"`
	CPUPercent    *MetricAverageDelta `json:"cpu_percent,omitempty"`
	MemoryPercent *MetricAverageDelta `json:"memory_percent,omitempty"`
	RXBPS         *MetricAverageDelta `json:"rx_bps,omitempty"`
	TXBPS         *MetricAverageDelta `json:"tx_bps,omitempty"`
}

type ConfigRevision struct {
	ID        string         `json:"id"`
	NodeID    string         `json:"node_id"`
	Revision  int64          `json:"revision"`
	Config    string         `json:"config"`
	SHA256    string         `json:"sha256"`
	Note      string         `json:"note,omitempty"`
	Metadata  map[string]any `json:"metadata"`
	CreatedBy string         `json:"created_by"`
	CreatedAt time.Time      `json:"created_at"`
}

type NodeConfigState struct {
	NodeID          string     `json:"node_id"`
	DesiredRevision *int64     `json:"desired_revision"`
	ActualRevision  *int64     `json:"actual_revision"`
	State           string     `json:"state"`
	LastError       string     `json:"last_error,omitempty"`
	LastReportAt    *time.Time `json:"last_report_at,omitempty"`
	UpdatedAt       time.Time  `json:"updated_at"`
}

type ApplyReport struct {
	ID                string                  `json:"id,omitempty"`
	NodeID            string                  `json:"node_id,omitempty"`
	Revision          int64                   `json:"revision"`
	State             string                  `json:"state"`
	ActualRevision    *int64                  `json:"actual_revision,omitempty"`
	Error             string                  `json:"error,omitempty"`
	RollbackAttempted bool                    `json:"rollback_attempted"`
	RollbackSucceeded *bool                   `json:"rollback_succeeded,omitempty"`
	Details           map[string]any          `json:"details,omitempty"`
	ReceivedAt        time.Time               `json:"received_at,omitempty"`
	MTLSNodeID        string                  `json:"-"`
	Credential        AgentCredentialIdentity `json:"-"`
}

// JobPayload is the versioned contract for future asynchronous bootstrap work.
type JobPayload struct {
	Version int            `json:"version"`
	Action  string         `json:"action"`
	Params  map[string]any `json:"params,omitempty"`
}
