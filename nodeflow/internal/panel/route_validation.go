package panel

import (
	"fmt"
	"net"
	"path"
	"strings"
	"unicode/utf8"
)

const (
	defaultRouteListenerIP   = "*"
	defaultRouteListenerPort = 443
	defaultRouteMatchMode    = "sni"
	defaultRouteTargetType   = "tcp"
	defaultProxyProtocol     = "none"
	defaultQuotaAction       = "observe"
	defaultQuotaPeriod       = "calendar_month"
)

type routeInput struct {
	ExpectedVersion *int64 `json:"expected_version"`
	Name            string `json:"name"`
	// Hostname is the legacy single-SNI field. New clients should use SNIs.
	Hostname       string   `json:"hostname"`
	ListenerIP     string   `json:"listener_ip"`
	ListenerPort   *int     `json:"listener_port"`
	MatchMode      string   `json:"match_mode"`
	SNIs           []string `json:"snis"`
	Fallback       bool     `json:"fallback"`
	TargetType     string   `json:"target_type"`
	TargetHost     string   `json:"target_host"`
	TargetPort     int      `json:"target_port"`
	UnixSocketPath string   `json:"unix_socket_path"`
	HealthCheck    *bool    `json:"health_check"`
	ProxyProtocol  string   `json:"proxy_protocol"`
	QuotaBytes     *int64   `json:"quota_bytes"`
	QuotaAction    string   `json:"quota_action"`
	QuotaPeriod    string   `json:"quota_period"`
	Enabled        *bool    `json:"enabled"`
	CustomFragment string   `json:"custom_fragment"`
}

func validateRoute(in routeInput, requireEnabled bool) (RouteSpec, error) {
	var out RouteSpec
	if in.ExpectedVersion != nil {
		if *in.ExpectedVersion < 1 {
			return out, fmt.Errorf("expected_version must be a positive integer")
		}
		version := *in.ExpectedVersion
		out.ExpectedVersion = &version
	}

	if containsForbiddenControl(in.Name) || containsForbiddenControl(in.ListenerIP) ||
		containsForbiddenControl(in.MatchMode) || containsForbiddenControl(in.TargetType) ||
		containsForbiddenControl(in.TargetHost) || containsForbiddenControl(in.UnixSocketPath) ||
		containsForbiddenControl(in.ProxyProtocol) || containsForbiddenControl(in.QuotaAction) ||
		containsForbiddenControl(in.QuotaPeriod) {
		return out, fmt.Errorf("route fields cannot contain control characters")
	}
	listenerIP := strings.TrimSpace(in.ListenerIP)
	if listenerIP == "" {
		listenerIP = defaultRouteListenerIP
	}
	if listenerIP != defaultRouteListenerIP {
		ip := net.ParseIP(listenerIP)
		if ip == nil {
			return out, fmt.Errorf("listener_ip must be * or an IP address")
		}
		listenerIP = ip.String()
	}
	listenerPort := defaultRouteListenerPort
	if in.ListenerPort != nil {
		listenerPort = *in.ListenerPort
	}
	if listenerPort < 1 || listenerPort > 65535 {
		return out, fmt.Errorf("listener_port must be between 1 and 65535")
	}
	out.ListenerIP, out.ListenerPort = listenerIP, listenerPort

	matchMode := strings.ToLower(strings.TrimSpace(in.MatchMode))
	if matchMode == "" {
		matchMode = defaultRouteMatchMode
		if in.Fallback {
			if wildcardListenerIP(listenerIP) {
				matchMode = "any_tcp"
			} else {
				matchMode = "destination_ip"
			}
		}
	}
	if matchMode != "any_tcp" && matchMode != "sni" && matchMode != "destination_ip" {
		return out, fmt.Errorf("match_mode must be any_tcp, sni or destination_ip")
	}
	if matchMode == "any_tcp" && !wildcardListenerIP(listenerIP) {
		return out, fmt.Errorf("match_mode any_tcp requires a wildcard listener_ip")
	}
	if matchMode == "destination_ip" && wildcardListenerIP(listenerIP) {
		return out, fmt.Errorf("match_mode destination_ip requires a concrete listener_ip")
	}
	out.MatchMode = matchMode

	legacyHostname := ""
	if strings.TrimSpace(in.Hostname) != "" {
		var ok bool
		legacyHostname, ok = normalizeDNSName(in.Hostname)
		if !ok {
			return out, fmt.Errorf("hostname must be a valid DNS name")
		}
	}
	if len(in.SNIs) > MaxRouteSNIs {
		return RouteSpec{}, fmt.Errorf("no more than %d SNI values are allowed", MaxRouteSNIs)
	}
	seen := make(map[string]struct{}, len(in.SNIs))
	for _, value := range in.SNIs {
		sni, ok := normalizeDNSName(value)
		if !ok {
			return out, fmt.Errorf("every SNI must be a valid DNS name")
		}
		if _, exists := seen[sni]; exists {
			return out, fmt.Errorf("SNI values must be unique")
		}
		seen[sni] = struct{}{}
		out.SNIs = append(out.SNIs, sni)
	}
	if len(out.SNIs) == 0 && legacyHostname != "" {
		out.SNIs = []string{legacyHostname}
	} else if legacyHostname != "" && out.SNIs[0] != legacyHostname {
		return RouteSpec{}, fmt.Errorf("hostname must equal the first SNI when both are provided")
	}
	if matchMode != "sni" {
		if len(out.SNIs) != 0 || legacyHostname != "" {
			return RouteSpec{}, fmt.Errorf("match_mode %s cannot contain SNI values", matchMode)
		}
		out.SNIs = []string{}
		out.Fallback = true
	} else {
		if in.Fallback {
			return RouteSpec{}, fmt.Errorf("match_mode sni cannot be fallback")
		}
		if len(out.SNIs) == 0 {
			return RouteSpec{}, fmt.Errorf("match_mode sni requires at least one SNI")
		}
	}
	if len(out.SNIs) > 0 {
		out.Hostname = out.SNIs[0]
	}

	name := strings.TrimSpace(in.Name)
	if name == "" {
		switch matchMode {
		case "sni":
			name = out.Hostname
		case "destination_ip":
			name = "ip-" + listenerIP + "-" + fmt.Sprint(listenerPort)
		default:
			name = "tcp-" + fmt.Sprint(listenerPort)
		}
	}
	if utf8.RuneCountInString(name) < 1 || utf8.RuneCountInString(name) > 80 {
		return RouteSpec{}, fmt.Errorf("name must contain between 1 and 80 characters")
	}
	out.Name = name

	targetType := strings.ToLower(strings.TrimSpace(in.TargetType))
	if targetType == "" {
		targetType = defaultRouteTargetType
	}
	out.TargetType = targetType
	switch targetType {
	case "tcp":
		if strings.TrimSpace(in.UnixSocketPath) != "" {
			return RouteSpec{}, fmt.Errorf("unix_socket_path is only valid for unix targets")
		}
		host, ok := normalizeTargetHost(in.TargetHost)
		if !ok || in.TargetPort < 1 || in.TargetPort > 65535 {
			return RouteSpec{}, fmt.Errorf("TCP target requires a valid target_host and target_port")
		}
		out.TargetHost, out.TargetPort = host, in.TargetPort
	case "unix":
		if strings.TrimSpace(in.TargetHost) != "" || in.TargetPort != 0 {
			return RouteSpec{}, fmt.Errorf("unix target cannot contain target_host or target_port")
		}
		socketPath := strings.TrimSpace(in.UnixSocketPath)
		if !validUnixSocketPath(socketPath) {
			return RouteSpec{}, fmt.Errorf("unix_socket_path must be a canonical absolute Linux socket path up to %d bytes", MaxUnixSocketPathBytes)
		}
		out.UnixSocketPath = socketPath
	default:
		return RouteSpec{}, fmt.Errorf("target_type must be tcp or unix")
	}
	out.HealthCheck = true
	if in.HealthCheck != nil {
		out.HealthCheck = *in.HealthCheck
	}

	proxyProtocol := strings.ToLower(strings.TrimSpace(in.ProxyProtocol))
	if proxyProtocol == "" {
		proxyProtocol = defaultProxyProtocol
	}
	if proxyProtocol != "none" && proxyProtocol != "v1" && proxyProtocol != "v2" {
		return RouteSpec{}, fmt.Errorf("proxy_protocol must be none, v1 or v2")
	}
	out.ProxyProtocol = proxyProtocol
	if in.QuotaBytes != nil {
		if *in.QuotaBytes <= 0 {
			return RouteSpec{}, fmt.Errorf("quota_bytes must be a positive integer or null")
		}
		quota := *in.QuotaBytes
		out.QuotaBytes = &quota
	}
	quotaAction := strings.ToLower(strings.TrimSpace(in.QuotaAction))
	if quotaAction == "" {
		quotaAction = defaultQuotaAction
	}
	if quotaAction != "observe" && quotaAction != "block_new" {
		return RouteSpec{}, fmt.Errorf("quota_action must be observe or block_new")
	}
	if quotaAction == "block_new" && out.QuotaBytes == nil {
		return RouteSpec{}, fmt.Errorf("quota_action block_new requires quota_bytes")
	}
	out.QuotaAction = quotaAction
	quotaPeriod := strings.ToLower(strings.TrimSpace(in.QuotaPeriod))
	if quotaPeriod == "" {
		quotaPeriod = defaultQuotaPeriod
	}
	if !validQuotaPeriod(quotaPeriod) {
		return RouteSpec{}, fmt.Errorf("quota_period must be hourly, daily, calendar_month or monthly_from_creation")
	}
	out.QuotaPeriod = quotaPeriod
	if requireEnabled && in.Enabled == nil {
		return RouteSpec{}, fmt.Errorf("enabled is required")
	}
	out.Enabled = true
	if in.Enabled != nil {
		out.Enabled = *in.Enabled
	}
	customFragment, err := normalizeCustomFragment(in.CustomFragment)
	if err != nil {
		return RouteSpec{}, err
	}
	out.CustomFragment = customFragment
	return out, nil
}

func validQuotaPeriod(value string) bool {
	switch value {
	case "hourly", "daily", "calendar_month", "monthly_from_creation":
		return true
	default:
		return false
	}
}

func normalizeDNSName(value string) (string, bool) {
	if containsForbiddenControl(value) {
		return "", false
	}
	value = strings.ToLower(strings.TrimSuffix(strings.TrimSpace(value), "."))
	return value, validDNSName(value)
}

func normalizeTargetHost(value string) (string, bool) {
	if containsForbiddenControl(value) {
		return "", false
	}
	value = strings.TrimSuffix(strings.TrimSpace(value), ".")
	if ip := net.ParseIP(value); ip != nil {
		return ip.String(), true
	}
	return normalizeDNSName(value)
}

func containsForbiddenControl(value string) bool {
	for _, char := range value {
		if char < 0x20 || char == 0x7f {
			return true
		}
	}
	return false
}

func validDNSName(value string) bool {
	if value == "" || len(value) > 253 || !utf8.ValidString(value) || strings.ContainsAny(value, "\r\n\t /:\\") {
		return false
	}
	for _, label := range strings.Split(value, ".") {
		if len(label) < 1 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, char := range label {
			if !((char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') || char == '-') {
				return false
			}
		}
	}
	return true
}

func validUnixSocketPath(value string) bool {
	if len(value) < 2 || len(value) > MaxUnixSocketPathBytes || !utf8.ValidString(value) || !path.IsAbs(value) || path.Clean(value) != value {
		return false
	}
	for _, char := range value {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || strings.ContainsRune("/._-", char)) {
			return false
		}
	}
	return true
}

// normalizeCustomFragment turns the legacy custom_fragment field into a
// canonical list of directives for this route's generated backend. HAProxy
// ignores indentation, so section headers have to be rejected after trimming.
// Backslashes are intentionally forbidden: HAProxy uses them for token and
// line escaping, which would make the route-level boundary ambiguous.
func normalizeCustomFragment(value string) (string, error) {
	if len(value) > MaxCustomFragmentBytes {
		return "", fmt.Errorf("custom_fragment must not exceed %d bytes", MaxCustomFragmentBytes)
	}
	if !utf8.ValidString(value) {
		return "", fmt.Errorf("custom_fragment must be valid UTF-8")
	}
	value = strings.ReplaceAll(value, "\r\n", "\n")
	for _, char := range value {
		if (char < 0x20 && char != '\n' && char != '\t') || char == 0x7f {
			return "", fmt.Errorf("custom_fragment contains a forbidden control character")
		}
	}
	if strings.ContainsRune(value, '\\') {
		return "", fmt.Errorf("custom_fragment cannot contain HAProxy escape characters")
	}

	lines := strings.Split(value, "\n")
	normalized := make([]string, 0, len(lines))
	blankPending := false
	for _, line := range lines {
		if len(line) > 512 {
			return "", fmt.Errorf("custom_fragment lines must not exceed 512 bytes")
		}
		directive := strings.TrimSpace(line)
		if directive == "" {
			if len(normalized) > 0 {
				blankPending = true
			}
			continue
		}
		fields := strings.Fields(strings.ToLower(directive))
		if len(fields) > 0 && forbiddenHAProxySection(fields[0]) {
			return "", fmt.Errorf("custom_fragment cannot declare HAProxy sections")
		}
		if blankPending {
			normalized = append(normalized, "")
			blankPending = false
		}
		normalized = append(normalized, "    "+directive)
	}
	result := strings.Join(normalized, "\n")
	if len(result) > MaxCustomFragmentBytes {
		return "", fmt.Errorf("custom_fragment must not exceed %d bytes after normalization", MaxCustomFragmentBytes)
	}
	return result, nil
}

func forbiddenHAProxySection(value string) bool {
	switch value {
	case "global", "defaults", "frontend", "backend", "listen", "peers", "resolvers", "userlist", "mailers", "cache", "program", "ring", "http-errors":
		return true
	default:
		return false
	}
}
