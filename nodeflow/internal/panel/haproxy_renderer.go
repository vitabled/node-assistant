package panel

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	HAProxyRendererVersion  = "haproxy-tcp-sni-v12"
	previousHAProxyRenderer = "haproxy-tcp-sni-v11"
	olderV10HAProxyRenderer = "haproxy-tcp-sni-v10"
	olderV9HAProxyRenderer  = "haproxy-tcp-sni-v9"
	olderV8HAProxyRenderer  = "haproxy-tcp-sni-v8"
	olderV7HAProxyRenderer  = "haproxy-tcp-sni-v7"
	legacyHAProxyRenderer   = "haproxy-tcp-sni-v6"
	olderV5HAProxyRenderer  = "haproxy-tcp-sni-v5"
	olderHAProxyRenderer    = "haproxy-tcp-sni-v4"
	olderV3HAProxyRenderer  = "haproxy-tcp-sni-v3"
	olderV2HAProxyRenderer  = "haproxy-tcp-sni-v2"
	initialHAProxyRenderer  = "haproxy-tcp-sni-v1"
	MaxRenderedRoutes       = 1024
)

var ErrNoEnabledRoutes = errors.New("no enabled routes")

func supportedHAProxyRenderers() []string {
	return []string{HAProxyRendererVersion, previousHAProxyRenderer, olderV10HAProxyRenderer, olderV9HAProxyRenderer, olderV8HAProxyRenderer, olderV7HAProxyRenderer, legacyHAProxyRenderer, olderV5HAProxyRenderer, olderHAProxyRenderer, olderV3HAProxyRenderer, olderV2HAProxyRenderer, initialHAProxyRenderer}
}

// RouteSetError means persisted route intent is internally inconsistent or
// unsafe to render. The renderer validates stored data again instead of
// relying only on HTTP and database constraints.
type RouteSetError struct {
	Reason string
}

func (e *RouteSetError) Error() string { return "invalid route set: " + e.Reason }

type RouteRuntimeNames struct {
	RouteID       string     `json:"route_id"`
	Frontend      string     `json:"frontend"`
	Backend       string     `json:"backend"`
	Server        string     `json:"server"`
	QuotaBytes    *int64     `json:"quota_bytes,omitempty"`
	QuotaAction   string     `json:"quota_action"`
	QuotaPeriod   string     `json:"quota_period"`
	QuotaAnchorAt *time.Time `json:"quota_anchor_at,omitempty"`
}

type HAProxyRenderResult struct {
	Config              string              `json:"config"`
	SHA256              string              `json:"sha256"`
	Renderer            string              `json:"renderer"`
	EnabledRoutes       int                 `json:"enabled_routes"`
	Listeners           int                 `json:"listeners"`
	QuotaMetadataRoutes int                 `json:"quota_metadata_routes"`
	QuotaRuntimeRoutes  int                 `json:"quota_runtime_routes"`
	ManualBackendRoutes int                 `json:"manual_backend_routes"`
	Warnings            []string            `json:"warnings"`
	ListenerPorts       []int               `json:"listener_ports"`
	RuntimeNames        []RouteRuntimeNames `json:"runtime_names"`
	RouteBackends       map[string]string   `json:"route_backends"`
	RouteFingerprints   map[string]string   `json:"route_fingerprints"`
}

type renderListener struct {
	IP     string
	Port   int
	Routes []renderRoute
}

type renderRoute struct {
	ID             string
	Name           string
	MatchMode      string
	SNIs           []string
	Fallback       bool
	TargetType     string
	TargetHost     string
	TargetPort     int
	UnixSocketPath string
	HealthCheck    bool
	ProxyProtocol  string
	QuotaBytes     *int64
	QuotaAction    string
	QuotaPeriod    string
	QuotaAnchorAt  *time.Time
	CustomFragment string
	CustomBytes    int
	Frontend       string
	Backend        string
	Server         string
}

// RenderHAProxyConfig deterministically turns validated route intent into a
// complete HAProxy TCP configuration. Disabled routes do not affect the data
// plane. custom_fragment is constrained to directives inside its own backend.
func RenderHAProxyConfig(routes []Route) (HAProxyRenderResult, error) {
	return renderHAProxyConfig(routes, false)
}

// renderHAProxyConfigForLifecycle permits a route-free configuration. HAProxy
// still receives a complete global/defaults configuration, which is required
// when the last active route is disabled or deleted. Operator preview keeps
// rejecting an empty route set so an accidental empty preview remains obvious.
func renderHAProxyConfigForLifecycle(routes []Route) (HAProxyRenderResult, error) {
	return renderHAProxyConfig(routes, true)
}

func renderHAProxyConfig(routes []Route, allowEmpty bool) (HAProxyRenderResult, error) {
	result := HAProxyRenderResult{
		Renderer:          HAProxyRendererVersion,
		Warnings:          []string{},
		RuntimeNames:      []RouteRuntimeNames{},
		RouteBackends:     map[string]string{},
		RouteFingerprints: map[string]string{},
	}

	groups := make(map[string]*renderListener)
	seenIDs := make(map[string]struct{})
	seenRuntimeNames := make(map[string]string)
	for _, route := range routes {
		if !route.Enabled {
			continue
		}
		if len(seenIDs) >= MaxRenderedRoutes {
			return HAProxyRenderResult{}, &RouteSetError{Reason: fmt.Sprintf("enabled route count exceeds %d", MaxRenderedRoutes)}
		}
		if !validID(route.ID) {
			return HAProxyRenderResult{}, &RouteSetError{Reason: "enabled route has an invalid id"}
		}
		if _, exists := seenIDs[route.ID]; exists {
			return HAProxyRenderResult{}, &RouteSetError{Reason: "duplicate route id " + route.ID}
		}
		seenIDs[route.ID] = struct{}{}

		enabled := true
		healthCheck := route.HealthCheck
		if route.MatchMode == "" {
			// Unit/legacy callers that predate persisted match_mode also predate
			// health_check; old renderers always emitted active TCP checks.
			healthCheck = true
		}
		listenerPort := route.ListenerPort
		spec, err := validateRoute(routeInput{
			Name:           route.Name,
			Hostname:       route.Hostname,
			ListenerIP:     route.ListenerIP,
			ListenerPort:   &listenerPort,
			MatchMode:      route.MatchMode,
			SNIs:           append([]string(nil), route.SNIs...),
			Fallback:       route.Fallback,
			TargetType:     route.TargetType,
			TargetHost:     route.TargetHost,
			TargetPort:     route.TargetPort,
			UnixSocketPath: route.UnixSocketPath,
			HealthCheck:    &healthCheck,
			ProxyProtocol:  route.ProxyProtocol,
			QuotaBytes:     route.QuotaBytes,
			QuotaAction:    route.QuotaAction,
			QuotaPeriod:    route.QuotaPeriod,
			Enabled:        &enabled,
			CustomFragment: route.CustomFragment,
		}, true)
		if err != nil {
			return HAProxyRenderResult{}, &RouteSetError{Reason: "route " + route.ID + " failed validation: " + err.Error()}
		}

		key := listenerKey(spec.ListenerIP, spec.ListenerPort)
		group := groups[key]
		if group == nil {
			group = &renderListener{IP: spec.ListenerIP, Port: spec.ListenerPort}
			groups[key] = group
		}
		backend := RouteBackendKey(route.ID)
		if previousID, exists := seenRuntimeNames[backend]; exists && previousID != route.ID {
			return HAProxyRenderResult{}, &RouteSetError{Reason: "runtime name collision between routes " + previousID + " and " + route.ID}
		}
		seenRuntimeNames[backend] = route.ID
		group.Routes = append(group.Routes, renderRoute{
			ID:             route.ID,
			Name:           spec.Name,
			MatchMode:      spec.MatchMode,
			SNIs:           spec.SNIs,
			Fallback:       spec.Fallback,
			TargetType:     spec.TargetType,
			TargetHost:     spec.TargetHost,
			TargetPort:     spec.TargetPort,
			UnixSocketPath: spec.UnixSocketPath,
			HealthCheck:    spec.HealthCheck,
			ProxyProtocol:  spec.ProxyProtocol,
			QuotaBytes:     spec.QuotaBytes,
			QuotaAction:    spec.QuotaAction,
			QuotaPeriod:    spec.QuotaPeriod,
			QuotaAnchorAt:  nonZeroTimePointer(route.CreatedAt),
			CustomFragment: spec.CustomFragment,
			CustomBytes:    len(spec.CustomFragment),
			Frontend:       frontendRuntimeName(spec.ListenerIP, spec.ListenerPort),
			Backend:        backend,
			Server:         RouteServerKey(route.ID),
		})
	}

	if len(groups) == 0 && !allowEmpty {
		return HAProxyRenderResult{}, ErrNoEnabledRoutes
	}
	if err := validateListenerBinds(groups); err != nil {
		return HAProxyRenderResult{}, err
	}

	listeners := make([]renderListener, 0, len(groups))
	for _, group := range groups {
		sort.Slice(group.Routes, func(i, j int) bool { return group.Routes[i].ID < group.Routes[j].ID })
		if err := validateListenerRoutes(*group); err != nil {
			return HAProxyRenderResult{}, err
		}
		listeners = append(listeners, *group)
	}
	sort.Slice(listeners, func(i, j int) bool {
		if listeners[i].IP != listeners[j].IP {
			return listeners[i].IP < listeners[j].IP
		}
		return listeners[i].Port < listeners[j].Port
	})
	hasDynamicDNS := false
	for _, listener := range listeners {
		for _, route := range listener.Routes {
			if route.TargetType == "tcp" && net.ParseIP(route.TargetHost) == nil {
				hasDynamicDNS = true
			}
		}
	}

	var b strings.Builder
	b.WriteString("# Generated by NodeFlow. Do not edit.\n")
	b.WriteString("# renderer: " + HAProxyRendererVersion + "\n\n")
	b.WriteString("global\n")
	b.WriteString("    log /dev/log local0\n")
	b.WriteString("    log /dev/log local1 notice\n")
	b.WriteString("    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners\n")
	b.WriteString("    stats timeout 30s\n")
	b.WriteString("    user haproxy\n")
	b.WriteString("    group haproxy\n")
	b.WriteByte('\n')
	b.WriteString("defaults\n")
	b.WriteString("    log global\n")
	b.WriteString("    mode tcp\n")
	b.WriteString("    option tcplog\n")
	b.WriteString("    option dontlognull\n")
	b.WriteString("    .if enabled(SPLICE)\n")
	b.WriteString("        option splice-auto\n")
	b.WriteString("    .endif\n")
	b.WriteString("    timeout connect 5s\n")
	b.WriteString("    timeout client 24h\n")
	b.WriteString("    timeout server 24h\n")
	if hasDynamicDNS {
		b.WriteString("\nresolvers nf_dns\n")
		b.WriteString("    nameserver adguard_local 127.0.0.1:53\n")
		b.WriteString("    nameserver systemd_resolved 127.0.0.53:53\n")
		b.WriteString("    nameserver cloudflare 1.1.1.1:53\n")
		b.WriteString("    nameserver google 8.8.8.8:53\n")
		b.WriteString("    nameserver quad9 9.9.9.9:53\n")
		b.WriteString("    resolve_retries 3\n")
		b.WriteString("    timeout resolve 1s\n")
		b.WriteString("    timeout retry 1s\n")
		b.WriteString("    hold valid 10s\n")
		b.WriteString("    hold obsolete 30s\n")
		b.WriteString("    hold nx 30s\n")
		b.WriteString("    hold timeout 30s\n")
		b.WriteString("    hold refused 30s\n")
	}

	for _, listener := range listeners {
		result.ListenerPorts = append(result.ListenerPorts, listener.Port)
		b.WriteString("\nfrontend " + frontendRuntimeName(listener.IP, listener.Port) + "\n")
		b.WriteString("    bind " + renderBind(listener.IP, listener.Port) + "\n")
		b.WriteString("    mode tcp\n")
		b.WriteString("    option tcplog\n")
		hasSNIRoute := false
		for _, route := range listener.Routes {
			if route.MatchMode == "sni" {
				hasSNIRoute = true
				break
			}
		}
		if hasSNIRoute {
			b.WriteString("    tcp-request inspect-delay 5s\n")
			b.WriteString("    tcp-request content accept if { req.ssl_hello_type 1 }\n")
		}
		var fallback *renderRoute
		for i := range listener.Routes {
			route := &listener.Routes[i]
			if route.MatchMode != "sni" {
				fallback = route
				continue
			}
			acl := routeACLName(route.ID)
			for _, sni := range route.SNIs {
				b.WriteString("    acl " + acl + " req.ssl_sni -i " + sni + "\n")
			}
			b.WriteString("    use_backend " + route.Backend + " if " + acl + "\n")
		}
		if fallback != nil {
			b.WriteString("    default_backend " + fallback.Backend + "\n")
		}
	}

	for _, listener := range listeners {
		for _, route := range listener.Routes {
			b.WriteString("\nbackend " + route.Backend + "\n")
			b.WriteString("    mode tcp\n")
			if route.HealthCheck {
				b.WriteString("    option tcp-check\n")
			}
			b.WriteString("    # nodeflow route_id=" + route.ID + "\n")
			if route.QuotaBytes != nil {
				enforcement := "metadata-only"
				if route.QuotaAction == "block_new" {
					enforcement = "runtime-block-new"
					result.QuotaRuntimeRoutes++
				} else {
					result.QuotaMetadataRoutes++
				}
				b.WriteString("    # quota=" + formatIECBytes(*route.QuotaBytes) + " enforcement=" + enforcement + "\n")
			}
			if route.CustomBytes > 0 {
				b.WriteString("    # manual_backend_directives_bytes=" + strconv.Itoa(route.CustomBytes) + "\n")
				b.WriteString(route.CustomFragment)
				b.WriteByte('\n')
				result.ManualBackendRoutes++
			}
			b.WriteString("    server " + route.Server + " " + renderTarget(route))
			if route.HealthCheck {
				b.WriteString(" check inter 5s fall 3 rise 2")
			}
			if route.TargetType == "tcp" && net.ParseIP(route.TargetHost) == nil {
				b.WriteString(" resolvers nf_dns init-addr last,none")
			}
			switch route.ProxyProtocol {
			case "v1":
				b.WriteString(" send-proxy")
			case "v2":
				b.WriteString(" send-proxy-v2")
			}
			b.WriteByte('\n')

			result.RuntimeNames = append(result.RuntimeNames, RouteRuntimeNames{
				RouteID: route.ID, Frontend: route.Frontend, Backend: route.Backend, Server: route.Server,
				QuotaBytes: route.QuotaBytes, QuotaAction: route.QuotaAction,
				QuotaPeriod: route.QuotaPeriod, QuotaAnchorAt: route.QuotaAnchorAt,
			})
			result.RouteBackends[route.ID] = route.Backend
			result.RouteFingerprints[route.ID] = routeSpecFingerprint(RouteSpec{
				Name: route.Name, ListenerIP: listener.IP, ListenerPort: listener.Port, MatchMode: route.MatchMode, SNIs: route.SNIs,
				Fallback: route.Fallback, TargetType: route.TargetType, TargetHost: route.TargetHost,
				TargetPort: route.TargetPort, UnixSocketPath: route.UnixSocketPath, HealthCheck: route.HealthCheck,
				ProxyProtocol: route.ProxyProtocol, QuotaBytes: route.QuotaBytes,
				QuotaAction: route.QuotaAction, QuotaPeriod: route.QuotaPeriod,
				Enabled: true, CustomFragment: route.CustomFragment,
			})
			result.EnabledRoutes++
		}
	}
	result.Listeners = len(listeners)
	if result.QuotaMetadataRoutes > 0 {
		result.Warnings = append(result.Warnings, "observe-only traffic quotas do not block HAProxy connections")
	}
	if result.QuotaRuntimeRoutes > 0 {
		result.Warnings = append(result.Warnings, "quota enforcement blocks new backend connections through the HAProxy Runtime API")
	}
	if result.ManualBackendRoutes > 0 {
		result.Warnings = append(result.Warnings, "manual backend directives are rendered and must pass HAProxy validation before apply")
	}
	result.Config = b.String()
	if len(result.Config) > MaxManagedConfigBytes {
		return HAProxyRenderResult{}, &RouteSetError{Reason: "rendered configuration exceeds size limit"}
	}
	sum := sha256.Sum256([]byte(result.Config))
	result.SHA256 = hex.EncodeToString(sum[:])
	return result, nil
}

func nonZeroTimePointer(value time.Time) *time.Time {
	if value.IsZero() {
		return nil
	}
	value = value.UTC()
	return &value
}

func validateListenerBinds(groups map[string]*renderListener) error {
	listeners := make([]*renderListener, 0, len(groups))
	for _, listener := range groups {
		listeners = append(listeners, listener)
	}
	for i := 0; i < len(listeners); i++ {
		for j := i + 1; j < len(listeners); j++ {
			left, right := listeners[i], listeners[j]
			if left.Port != right.Port || left.IP == right.IP {
				continue
			}
			if wildcardListenerIP(left.IP) || wildcardListenerIP(right.IP) {
				return &RouteSetError{Reason: fmt.Sprintf("listener binds %s and %s overlap", listenerKey(left.IP, left.Port), listenerKey(right.IP, right.Port))}
			}
		}
	}
	return nil
}

func wildcardListenerIP(ip string) bool {
	return ip == "*" || ip == "0.0.0.0" || ip == "::"
}

func validateListenerRoutes(listener renderListener) error {
	seenSNI := make(map[string]string)
	fallbackID := ""
	for _, route := range listener.Routes {
		if route.MatchMode != "sni" {
			if fallbackID != "" {
				return &RouteSetError{Reason: "listener " + listenerKey(listener.IP, listener.Port) + " has multiple fallback routes"}
			}
			fallbackID = route.ID
			continue
		}
		for _, sni := range route.SNIs {
			if previous, exists := seenSNI[sni]; exists {
				return &RouteSetError{Reason: "listener " + listenerKey(listener.IP, listener.Port) + " assigns SNI " + sni + " to routes " + previous + " and " + route.ID}
			}
			seenSNI[sni] = route.ID
		}
	}
	return nil
}

func listenerKey(ip string, port int) string {
	return ip + "|" + strconv.Itoa(port)
}

func frontendRuntimeName(ip string, port int) string {
	name := "any"
	if ip != "*" {
		name = sanitizeRuntimeName(ip)
	}
	sum := sha256.Sum256([]byte(listenerKey(ip, port)))
	return "nf_fe_" + name + "_" + strconv.Itoa(port) + "_" + hex.EncodeToString(sum[:4])
}

func routeACLName(id string) string {
	return "nf_sni_" + routeRuntimeID(id)
}

func formatIECBytes(bytes int64) string {
	units := [...]string{"B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"}
	value := float64(bytes)
	unit := 0
	for value >= 1024 && unit < len(units)-1 {
		value /= 1024
		unit++
	}
	precision := 2
	if value >= 100 || unit == 0 {
		precision = 0
	} else if value >= 10 {
		precision = 1
	}
	formatted := strconv.FormatFloat(value, 'f', precision, 64)
	if strings.Contains(formatted, ".") {
		formatted = strings.TrimRight(strings.TrimRight(formatted, "0"), ".")
	}
	return formatted + " " + units[unit]
}

func sanitizeRuntimeName(value string) string {
	var b strings.Builder
	underscore := false
	for _, r := range strings.ToLower(value) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
			underscore = false
			continue
		}
		if !underscore {
			b.WriteByte('_')
			underscore = true
		}
	}
	return strings.Trim(b.String(), "_")
}

func renderBind(ip string, port int) string {
	if ip == "*" {
		return ":" + strconv.Itoa(port)
	}
	if strings.ContainsRune(ip, ':') {
		return "[" + ip + "]:" + strconv.Itoa(port)
	}
	return ip + ":" + strconv.Itoa(port)
}

func renderTarget(route renderRoute) string {
	if route.TargetType == "unix" {
		return route.UnixSocketPath
	}
	if strings.ContainsRune(route.TargetHost, ':') {
		return "[" + route.TargetHost + "]:" + strconv.Itoa(route.TargetPort)
	}
	return route.TargetHost + ":" + strconv.Itoa(route.TargetPort)
}

func renderMetadata(result HAProxyRenderResult) map[string]any {
	quotaEnforcement := "none"
	if result.QuotaMetadataRoutes > 0 {
		quotaEnforcement = "metadata_only"
	}
	if result.QuotaRuntimeRoutes > 0 {
		quotaEnforcement = "runtime_block_new"
		if result.QuotaMetadataRoutes > 0 {
			quotaEnforcement = "mixed"
		}
	}
	return map[string]any{
		"source":                 "routes",
		"renderer":               result.Renderer,
		"enabled_routes":         result.EnabledRoutes,
		"listeners":              result.Listeners,
		"listener_tcp_ports":     result.ListenerPorts,
		"quota_enforcement":      quotaEnforcement,
		"custom_fragment_policy": "route_backend_directives",
		"manual_backend_routes":  result.ManualBackendRoutes,
		"route_backends":         result.RouteBackends,
		"route_fingerprints":     result.RouteFingerprints,
		"runtime_names":          result.RuntimeNames,
	}
}

func renderErrorResponse(err error) (status int, code, message string) {
	if errors.Is(err, ErrNoEnabledRoutes) {
		return 422, "no_enabled_routes", "node has no enabled routes to render"
	}
	var invalid *RouteSetError
	if errors.As(err, &invalid) {
		return 422, "invalid_route_set", invalid.Error()
	}
	return 500, "internal_error", "internal server error"
}

func routeRenderSummary(result HAProxyRenderResult) string {
	return fmt.Sprintf("маршрутов %d, listener-ов %d", result.EnabledRoutes, result.Listeners)
}
