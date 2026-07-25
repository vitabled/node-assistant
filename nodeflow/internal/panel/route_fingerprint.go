package panel

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

// routeSpecFingerprint identifies the operator-visible desired route shape.
// Deployment state, revision numbers, timestamps and optimistic version are
// deliberately excluded. Struct field order makes the JSON input stable.
func routeSpecFingerprint(spec RouteSpec) string {
	payload := struct {
		Name           string   `json:"name"`
		ListenerIP     string   `json:"listener_ip"`
		ListenerPort   int      `json:"listener_port"`
		MatchMode      string   `json:"match_mode"`
		SNIs           []string `json:"snis"`
		Fallback       bool     `json:"fallback"`
		TargetType     string   `json:"target_type"`
		TargetHost     string   `json:"target_host"`
		TargetPort     int      `json:"target_port"`
		UnixSocketPath string   `json:"unix_socket_path"`
		HealthCheck    bool     `json:"health_check"`
		ProxyProtocol  string   `json:"proxy_protocol"`
		QuotaBytes     *int64   `json:"quota_bytes"`
		QuotaAction    string   `json:"quota_action"`
		QuotaPeriod    string   `json:"quota_period"`
		Enabled        bool     `json:"enabled"`
		CustomFragment string   `json:"custom_fragment"`
	}{
		Name: spec.Name, ListenerIP: spec.ListenerIP, ListenerPort: spec.ListenerPort, MatchMode: spec.MatchMode, SNIs: spec.SNIs,
		Fallback: spec.Fallback, TargetType: spec.TargetType, TargetHost: spec.TargetHost,
		TargetPort: spec.TargetPort, UnixSocketPath: spec.UnixSocketPath, HealthCheck: spec.HealthCheck,
		ProxyProtocol: spec.ProxyProtocol, QuotaBytes: spec.QuotaBytes,
		QuotaAction: spec.QuotaAction, QuotaPeriod: spec.QuotaPeriod,
		Enabled: spec.Enabled, CustomFragment: spec.CustomFragment,
	}
	encoded, _ := json.Marshal(payload)
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:])
}

func routeAsSpec(route Route, enabled bool) RouteSpec {
	return RouteSpec{
		Name: route.Name, ListenerIP: route.ListenerIP, ListenerPort: route.ListenerPort, MatchMode: route.MatchMode,
		SNIs: append([]string(nil), route.SNIs...), Fallback: route.Fallback, Hostname: route.Hostname,
		TargetType: route.TargetType, TargetHost: route.TargetHost, TargetPort: route.TargetPort,
		UnixSocketPath: route.UnixSocketPath, HealthCheck: route.HealthCheck, ProxyProtocol: route.ProxyProtocol,
		QuotaBytes: route.QuotaBytes, QuotaAction: route.QuotaAction, QuotaPeriod: route.QuotaPeriod, Enabled: enabled,
		CustomFragment: route.CustomFragment,
	}
}

// normalizeRouteSpecDefaults keeps internal/legacy callers compatible while
// preserving an explicit health_check=false from the v6 API. HTTP validation
// always sets MatchMode, so an empty mode identifies a pre-v6 caller.
func normalizeRouteSpecDefaults(spec *RouteSpec) {
	legacy := spec.MatchMode == ""
	if legacy {
		if spec.Fallback {
			if wildcardListenerIP(spec.ListenerIP) {
				spec.MatchMode = "any_tcp"
			} else {
				spec.MatchMode = "destination_ip"
			}
		} else {
			spec.MatchMode = "sni"
		}
		spec.HealthCheck = true
	}
	if spec.Name == "" {
		switch spec.MatchMode {
		case "sni":
			spec.Name = spec.Hostname
		case "destination_ip":
			spec.Name = "ip-" + spec.ListenerIP + "-" + fmt.Sprint(spec.ListenerPort)
		default:
			spec.Name = "tcp-" + fmt.Sprint(spec.ListenerPort)
		}
	}
}
