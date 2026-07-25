// Contract types for the HAPROXY (NodeFlow) sections. Ported from NodeFlow's
// frontend/src/lib/contracts.ts — these mirror the panel's `/api/v1/*` responses,
// which node-installer reaches through the /api/haproxy/proxy/* backend proxy.

export type NodeStatus = "online" | "offline" | "pending" | "error";

export interface NodeRecord {
  id: string;
  name: string;
  address: string;
  status: NodeStatus | string;
  metadata: Record<string, unknown> | null;
  last_seen_at?: string;
  created_at: string;
  updated_at: string;
}

export interface HAProxyProxyStats {
  status?: string;
  connections_current?: number;
  sessions_current?: number;
  sessions_total?: number;
  session_rate?: number;
  rx_bps?: number;
  tx_bps?: number;
  bytes_in?: number;
  bytes_out?: number;
}

export interface HAProxyServerStats {
  status?: string;
  check_status?: string;
  check_description?: string;
  sessions_current?: number;
  sessions_total?: number;
  active?: boolean;
  backup?: boolean;
}

export interface HAProxyRuntime {
  connections_current?: number;
  connections_total?: number;
  connection_rate?: number;
  bytes_in?: number;
  bytes_out?: number;
  backends?: Record<string, HAProxyProxyStats>;
  servers?: Record<string, Record<string, HAProxyServerStats>>;
}

export interface HeartbeatMetrics {
  os?: string;
  arch?: string;
  cpu_count?: number;
  cpu_percent?: number;
  load?: number[];
  memory_total_bytes?: number;
  memory_available_bytes?: number;
  memory_percent?: number;
  network_bytes_per_second?: Record<string, number>;
  uptime_seconds?: number;
  process_count?: number;
  haproxy_version?: string;
  haproxy_stats_available?: boolean;
  haproxy_runtime?: HAProxyRuntime;
  [key: string]: unknown;
}

export interface NodeHeartbeat {
  agent_version: string;
  status: string;
  metrics: HeartbeatMetrics;
  routes_ok?: boolean;
  received_at: string;
}

export interface HAProxyControlState {
  node_id: string;
  supported: boolean;
  desired_enabled: boolean;
  generation: number;
  actual_enabled?: boolean;
  active_state:
    | "unknown" | "active" | "reloading" | "inactive"
    | "failed" | "activating" | "deactivating" | string;
  report_generation: number;
  last_error?: string;
  reported_at?: string;
  updated_at?: string;
}

export interface NodeOperational {
  node: NodeRecord;
  latest_heartbeat?: NodeHeartbeat;
  routes_total: number;
  routes_enabled: number;
  traffic_month: string;
  traffic_bytes_in: number;
  traffic_bytes_out: number;
  traffic_used_bytes: number;
  traffic_observed: boolean;
  rx_bits_per_second?: number | null;
  tx_bits_per_second?: number | null;
  credential_prefix?: string;
  credential_expires_at?: string;
  haproxy_control?: HAProxyControlState;
}

export interface RouteRecord {
  id: string;
  node_id: string;
  name: string;
  version: number;
  listener_ip: string;
  listener_port: number;
  match_mode: "any_tcp" | "sni" | "destination_ip";
  snis: string[];
  fallback: boolean;
  hostname?: string;
  target_type: "tcp" | "domain" | "unix" | string;
  target_host: string;
  target_port: number;
  unix_socket_path: string;
  health_check: boolean;
  proxy_protocol: "none" | "v1" | "v2" | string;
  quota_bytes: number | null;
  quota_action: string;
  quota_period: string;
  enabled: boolean;
  deployed: boolean;
  deployment_state: string;
  deployment_error?: string;
  custom_fragment?: string;
  created_at?: string;
  updated_at?: string;
}

export interface TrafficRoute {
  route_id: string;
  backend_key: string;
  bytes_in: number;
  bytes_out: number;
  used_bytes: number;
  quota_used_bytes: number;
  limit_bytes: number | null;
  quota_period?: string;
  reached: boolean;
  quota_action?: string;
  enforcement?: boolean;
  blocked?: boolean;
  observed: boolean;
}

export interface NodeTraffic {
  node_id: string;
  month: string;
  bytes_in: number;
  bytes_out: number;
  used_bytes: number;
  enforcement: boolean;
  routes: TrafficRoute[];
}

export interface TrafficHistorySample {
  timestamp: string;
  rx_bps: number | null;
  tx_bps: number | null;
  cpu_percent?: number;
  memory_percent?: number;
}

export interface TrafficHistory {
  range: string;
  bucket_seconds: number;
  samples: TrafficHistorySample[];
}

export interface DashboardNode {
  node: NodeRecord;
  latest_heartbeat?: NodeHeartbeat;
  routes_total: number;
  routes_enabled: number;
  traffic_used_bytes: number;
  traffic_observed: boolean;
  rx_bits_per_second: number | null;
  tx_bits_per_second: number | null;
}

export interface DashboardRoute {
  route_id: string;
  node_id: string;
  node_name: string;
  name: string;
  listener_ip: string;
  listener_port: number;
  snis: string[];
  fallback: boolean;
  used_bytes: number;
  bits_per_second: number;
  share_percent: number;
}

export interface DashboardOverviewTotal {
  nodes_total: number;
  nodes_online: number;
  nodes_degraded: number;
  nodes_offline: number;
  routes_total: number;
  connections_current: number;
  rx_bits_per_second: number | null;
  tx_bits_per_second: number | null;
  traffic_month_bytes: number;
  backends_healthy: number;
  backends_degraded: number;
  backends_unavailable: number;
}

export interface DashboardOverview {
  range: string;
  nodes: DashboardNode[];
  traffic_history: TrafficHistory;
  top_routes: DashboardRoute[];
  totals: DashboardOverviewTotal;
}

export type FirewallMode = "off" | "observe" | "apply";

export interface NodeFirewallPolicy {
  node_id: string;
  mode: FirewallMode;
  tcp_ports: number[];
  plan_complete: boolean;
  updated_at: string;
}

export interface AgentRelease {
  id: string;
  version: string;
  os: string;
  arch: string;
  sha256: string;
  size_bytes: number;
  sequence: number;
  signature: string;
  created_at: string;
}

export type BootstrapAuthMode = "password" | "private_key";
export type BootstrapSudoMode = "auto" | "root" | "password" | "passwordless";

export interface HostKeyResult {
  algorithm: "ssh-ed25519" | "ecdsa-sha2-nistp256" | "rsa-sha2-256";
  fingerprint: string;
  os?: string;
  arch?: string;
}

export interface BootstrapNodeRequest {
  name: string;
  address: string;
  ssh_port: number;
  username: string;
  auth_mode: BootstrapAuthMode;
  password?: string;
  private_key?: string;
  private_key_passphrase?: string;
  sudo_mode: BootstrapSudoMode;
  sudo_password?: string;
  agent_port: number;
  host_key_sha256: string;
  host_key_algorithm: HostKeyResult["algorithm"];
  allow_firewall_apply: boolean;
  release_id?: string;
}

export type BootstrapJobStatus = "queued" | "running" | "installed" | "failed";

export interface BootstrapJobResponse {
  job_id: string;
  status: BootstrapJobStatus;
  stage: string;
  node_id?: string;
  created_at: string;
  updated_at: string;
  failure_summary?: string;
  failure_code?: string;
  exit_code?: number;
}

// Our backend's /api/haproxy/config shape.
export interface HaproxyConnState {
  enabled: boolean;
  base_url: string;
  has_token: boolean;
  configured: boolean;
}
