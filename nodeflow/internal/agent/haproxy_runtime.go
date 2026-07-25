package agent

import (
	"bytes"
	"context"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const (
	defaultHAProxyStatsTimeout = 2 * time.Second
	defaultHAProxyMaxResponse  = 8 << 20
	maxHAProxyMetricNameBytes  = 128
	maxHAProxyMetricTextBytes  = 256
	maxHAProxyFrontendMetrics  = 1024
	maxHAProxyBackendMetrics   = 1024
	maxHAProxyServerMetrics    = 1024
)

// HAProxyRuntimeCollector is intentionally small so heartbeat degradation can
// be tested without a real HAProxy process.
type HAProxyRuntimeCollector interface {
	Collect(context.Context) (HAProxyRuntimeStats, error)
}

// HAProxyRuntimeStats is the stable, typed subset of the HAProxy CLI exposed to
// the panel. Raw CLI output is never retained or sent.
type HAProxyRuntimeStats struct {
	CounterGeneration  string                                   `json:"counter_generation,omitempty"`
	ConnectionsCurrent uint64                                   `json:"connections_current"`
	ConnectionsTotal   uint64                                   `json:"connections_total"`
	ConnectionRate     uint64                                   `json:"connection_rate"`
	BytesIn            uint64                                   `json:"bytes_in"`
	BytesOut           uint64                                   `json:"bytes_out"`
	Frontends          map[string]HAProxyProxyStats             `json:"frontends"`
	Backends           map[string]HAProxyProxyStats             `json:"backends"`
	Servers            map[string]map[string]HAProxyServerStats `json:"servers"`
	Truncated          bool                                     `json:"truncated,omitempty"`
}

type HAProxyProxyStats struct {
	Status          string `json:"status,omitempty"`
	SessionsCurrent uint64 `json:"sessions_current"`
	SessionsTotal   uint64 `json:"sessions_total"`
	SessionRate     uint64 `json:"session_rate"`
	SessionLimit    uint64 `json:"session_limit"`
	QueueCurrent    uint64 `json:"queue_current"`
	QueueMax        uint64 `json:"queue_max"`
	BytesIn         uint64 `json:"bytes_in"`
	BytesOut        uint64 `json:"bytes_out"`
}

type HAProxyServerStats struct {
	Status            string `json:"status,omitempty"`
	CheckStatus       string `json:"check_status,omitempty"`
	CheckCode         uint64 `json:"check_code,omitempty"`
	CheckDurationMS   uint64 `json:"check_duration_ms,omitempty"`
	CheckDescription  string `json:"check_description,omitempty"`
	SessionsCurrent   uint64 `json:"sessions_current"`
	SessionsTotal     uint64 `json:"sessions_total"`
	SessionRate       uint64 `json:"session_rate"`
	QueueCurrent      uint64 `json:"queue_current"`
	QueueMax          uint64 `json:"queue_max"`
	BytesIn           uint64 `json:"bytes_in"`
	BytesOut          uint64 `json:"bytes_out"`
	LastChangeSeconds uint64 `json:"last_change_seconds,omitempty"`
	DowntimeSeconds   uint64 `json:"downtime_seconds,omitempty"`
	Weight            uint64 `json:"weight,omitempty"`
	Active            bool   `json:"active"`
	Backup            bool   `json:"backup"`
}

// HAProxySocketClient reads the read-only HAProxy runtime CLI over a Unix
// socket. Timeout is a budget for the complete info+stat collection, not for
// each individual command.
type HAProxySocketClient struct {
	Path             string
	Timeout          time.Duration
	MaxResponseBytes int64
}

func (c HAProxySocketClient) Collect(ctx context.Context) (HAProxyRuntimeStats, error) {
	stats := emptyHAProxyRuntimeStats()
	if err := validateHAProxySocketPath(c.Path); err != nil {
		return stats, err
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = defaultHAProxyStatsTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	infoResponse, infoQueryErr := c.query(ctx, "show info")
	statResponse, statQueryErr := c.query(ctx, "show stat")
	infoOK := false
	statOK := false
	var info showInfoMetrics
	if infoQueryErr == nil {
		var err error
		info, err = parseHAProxyShowInfo(infoResponse)
		infoOK = err == nil
	}
	if statQueryErr == nil {
		parsed, err := parseHAProxyShowStat(statResponse)
		if err == nil {
			stats = parsed
			statOK = true
		}
	}
	// Traffic accounting is derived from show stat cumulative counters. A
	// successful show info response must never turn a failed/invalid show stat
	// response into an all-zero snapshot: Panel would interpret that as a
	// counter reset and charge the next healthy snapshot twice.
	if !statOK {
		return emptyHAProxyRuntimeStats(), errors.New("HAProxy runtime stats unavailable")
	}
	if infoOK {
		stats.CounterGeneration = info.counterGeneration()
		if info.hasConnectionsCurrent {
			stats.ConnectionsCurrent = info.connectionsCurrent
		}
		if info.hasConnectionsTotal {
			stats.ConnectionsTotal = info.connectionsTotal
		}
		if info.hasConnectionRate {
			stats.ConnectionRate = info.connectionRate
		}
	}
	return stats, nil
}

func (c HAProxySocketClient) query(ctx context.Context, command string) ([]byte, error) {
	if command != "show info" && command != "show stat" {
		return nil, errors.New("unsupported HAProxy CLI command")
	}
	return c.queryRaw(ctx, command)
}

func (c HAProxySocketClient) queryRaw(ctx context.Context, command string) ([]byte, error) {
	maxResponse := c.MaxResponseBytes
	if maxResponse <= 0 {
		maxResponse = defaultHAProxyMaxResponse
	}
	dialer := net.Dialer{}
	conn, err := dialer.DialContext(ctx, "unix", c.Path)
	if err != nil {
		return nil, errors.New("HAProxy stats socket unavailable")
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		if err := conn.SetDeadline(deadline); err != nil {
			return nil, errors.New("cannot set HAProxy stats socket deadline")
		}
	}
	if _, err := io.WriteString(conn, command+"\n"); err != nil {
		return nil, errors.New("cannot write HAProxy stats command")
	}
	if closeWriter, ok := conn.(interface{ CloseWrite() error }); ok {
		if err := closeWriter.CloseWrite(); err != nil {
			return nil, errors.New("cannot finish HAProxy stats command")
		}
	}
	response, err := io.ReadAll(io.LimitReader(conn, maxResponse+1))
	if err != nil {
		return nil, errors.New("cannot read HAProxy stats response")
	}
	if int64(len(response)) > maxResponse {
		return nil, errors.New("HAProxy stats response exceeds limit")
	}
	return response, nil
}

func (c HAProxySocketClient) SetServerMaintenance(ctx context.Context, backend, server string, maintenance bool) error {
	if err := validateHAProxySocketPath(c.Path); err != nil {
		return err
	}
	if !validRuntimeObjectName(backend) || !validRuntimeObjectName(server) {
		return errors.New("invalid HAProxy runtime object name")
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = defaultHAProxyStatsTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	verb := "enable"
	if maintenance {
		verb = "disable"
	}
	response, err := c.queryRaw(ctx, verb+" server "+backend+"/"+server)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(response)) != "" {
		return errors.New("HAProxy rejected runtime server state change")
	}
	return nil
}

func validRuntimeObjectName(value string) bool {
	if value == "" || len(value) > maxHAProxyMetricNameBytes {
		return false
	}
	for _, char := range value {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') ||
			(char >= '0' && char <= '9') || char == '_' || char == '-' || char == '.' || char == ':') {
			return false
		}
	}
	return true
}

func validateHAProxySocketPath(path string) error {
	if path == "" || !filepath.IsAbs(path) || strings.ContainsRune(path, '\x00') || len(path) > 107 {
		return errors.New("invalid HAProxy stats socket path")
	}
	return nil
}

type showInfoMetrics struct {
	pid                   uint64
	startTimeSeconds      uint64
	reloads               uint64
	connectionsCurrent    uint64
	connectionsTotal      uint64
	connectionRate        uint64
	hasPID                bool
	hasStartTimeSeconds   bool
	hasReloads            bool
	hasConnectionsCurrent bool
	hasConnectionsTotal   bool
	hasConnectionRate     bool
}

func parseHAProxyShowInfo(response []byte) (showInfoMetrics, error) {
	var result showInfoMetrics
	for _, line := range bytes.Split(response, []byte{'\n'}) {
		keyBytes, valueBytes, ok := bytes.Cut(line, []byte{':'})
		if !ok {
			continue
		}
		key := strings.TrimSpace(string(keyBytes))
		var target *uint64
		var present *bool
		switch key {
		case "Pid":
			target, present = &result.pid, &result.hasPID
		case "Start_time_sec":
			target, present = &result.startTimeSeconds, &result.hasStartTimeSeconds
		case "Reloads":
			target, present = &result.reloads, &result.hasReloads
		case "CurrConns":
			target, present = &result.connectionsCurrent, &result.hasConnectionsCurrent
		case "CumConns":
			target, present = &result.connectionsTotal, &result.hasConnectionsTotal
		case "ConnRate":
			target, present = &result.connectionRate, &result.hasConnectionRate
		default:
			continue
		}
		value, err := strconv.ParseUint(strings.TrimSpace(string(valueBytes)), 10, 64)
		if err != nil {
			return showInfoMetrics{}, fmt.Errorf("invalid HAProxy show info field %s", key)
		}
		*target = value
		*present = true
	}
	if !result.hasConnectionsCurrent && !result.hasConnectionsTotal && !result.hasConnectionRate {
		return showInfoMetrics{}, errors.New("HAProxy show info has no connection metrics")
	}
	return result, nil
}

func (m showInfoMetrics) counterGeneration() string {
	if !m.hasPID || !m.hasStartTimeSeconds {
		return ""
	}
	return strconv.FormatUint(m.pid, 10) + ":" +
		strconv.FormatUint(m.startTimeSeconds, 10) + ":" +
		strconv.FormatUint(m.reloads, 10)
}

func parseHAProxyShowStat(response []byte) (HAProxyRuntimeStats, error) {
	stats := emptyHAProxyRuntimeStats()
	reader := csv.NewReader(bytes.NewReader(response))
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true

	var columns map[string]int
	frontendCount, backendCount, serverCount := 0, 0, 0
	for {
		record, err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return emptyHAProxyRuntimeStats(), errors.New("invalid HAProxy show stat CSV")
		}
		if len(record) == 0 || (len(record) == 1 && strings.TrimSpace(record[0]) == "") {
			continue
		}
		if columns == nil {
			columns = make(map[string]int, len(record))
			for i, name := range record {
				name = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(name), "#"))
				if name != "" {
					columns[name] = i
				}
			}
			if _, ok := columns["pxname"]; !ok {
				return emptyHAProxyRuntimeStats(), errors.New("HAProxy show stat CSV missing pxname")
			}
			if _, ok := columns["svname"]; !ok {
				return emptyHAProxyRuntimeStats(), errors.New("HAProxy show stat CSV missing svname")
			}
			continue
		}

		proxyName, proxyOK := metricName(csvField(record, columns, "pxname"))
		serviceName, serviceOK := metricName(csvField(record, columns, "svname"))
		if !proxyOK || !serviceOK {
			stats.Truncated = true
			continue
		}
		objectType := csvField(record, columns, "type")
		switch {
		case strings.EqualFold(serviceName, "FRONTEND") || objectType == "0":
			row, parseErr := proxyStatsFromCSV(record, columns)
			if parseErr != nil {
				return emptyHAProxyRuntimeStats(), parseErr
			}
			stats.BytesIn = saturatingAdd(stats.BytesIn, row.BytesIn)
			stats.BytesOut = saturatingAdd(stats.BytesOut, row.BytesOut)
			stats.ConnectionsCurrent = saturatingAdd(stats.ConnectionsCurrent, row.SessionsCurrent)
			stats.ConnectionsTotal = saturatingAdd(stats.ConnectionsTotal, row.SessionsTotal)
			stats.ConnectionRate = saturatingAdd(stats.ConnectionRate, row.SessionRate)
			if _, exists := stats.Frontends[proxyName]; !exists {
				if frontendCount >= maxHAProxyFrontendMetrics {
					stats.Truncated = true
					continue
				}
				frontendCount++
			}
			stats.Frontends[proxyName] = row
		case strings.EqualFold(serviceName, "BACKEND") || objectType == "1":
			row, parseErr := proxyStatsFromCSV(record, columns)
			if parseErr != nil {
				return emptyHAProxyRuntimeStats(), parseErr
			}
			if _, exists := stats.Backends[proxyName]; !exists {
				if backendCount >= maxHAProxyBackendMetrics {
					stats.Truncated = true
					continue
				}
				backendCount++
			}
			stats.Backends[proxyName] = row
		case objectType == "2":
			row, parseErr := serverStatsFromCSV(record, columns)
			if parseErr != nil {
				return emptyHAProxyRuntimeStats(), parseErr
			}
			servers := stats.Servers[proxyName]
			if servers == nil {
				servers = make(map[string]HAProxyServerStats)
				stats.Servers[proxyName] = servers
			}
			if _, exists := servers[serviceName]; !exists {
				if serverCount >= maxHAProxyServerMetrics {
					stats.Truncated = true
					continue
				}
				serverCount++
			}
			servers[serviceName] = row
		}
	}
	if columns == nil {
		return emptyHAProxyRuntimeStats(), errors.New("empty HAProxy show stat CSV")
	}
	return stats, nil
}

func emptyHAProxyRuntimeStats() HAProxyRuntimeStats {
	return HAProxyRuntimeStats{
		Frontends: make(map[string]HAProxyProxyStats),
		Backends:  make(map[string]HAProxyProxyStats),
		Servers:   make(map[string]map[string]HAProxyServerStats),
	}
}

func proxyStatsFromCSV(record []string, columns map[string]int) (HAProxyProxyStats, error) {
	bytesIn, err := csvTrafficCounter(record, columns, "bin")
	if err != nil {
		return HAProxyProxyStats{}, err
	}
	bytesOut, err := csvTrafficCounter(record, columns, "bout")
	if err != nil {
		return HAProxyProxyStats{}, err
	}
	return HAProxyProxyStats{
		Status:          metricText(csvField(record, columns, "status")),
		SessionsCurrent: csvUint(record, columns, "scur"),
		SessionsTotal:   csvUint(record, columns, "stot"),
		SessionRate:     csvUint(record, columns, "rate"),
		SessionLimit:    csvUint(record, columns, "slim"),
		QueueCurrent:    csvUint(record, columns, "qcur"),
		QueueMax:        csvUint(record, columns, "qmax"),
		BytesIn:         bytesIn,
		BytesOut:        bytesOut,
	}, nil
}

func serverStatsFromCSV(record []string, columns map[string]int) (HAProxyServerStats, error) {
	bytesIn, err := csvTrafficCounter(record, columns, "bin")
	if err != nil {
		return HAProxyServerStats{}, err
	}
	bytesOut, err := csvTrafficCounter(record, columns, "bout")
	if err != nil {
		return HAProxyServerStats{}, err
	}
	return HAProxyServerStats{
		Status:            metricText(csvField(record, columns, "status")),
		CheckStatus:       metricText(csvField(record, columns, "check_status")),
		CheckCode:         csvUint(record, columns, "check_code"),
		CheckDurationMS:   csvUint(record, columns, "check_duration"),
		CheckDescription:  metricText(csvField(record, columns, "check_desc")),
		SessionsCurrent:   csvUint(record, columns, "scur"),
		SessionsTotal:     csvUint(record, columns, "stot"),
		SessionRate:       csvUint(record, columns, "rate"),
		QueueCurrent:      csvUint(record, columns, "qcur"),
		QueueMax:          csvUint(record, columns, "qmax"),
		BytesIn:           bytesIn,
		BytesOut:          bytesOut,
		LastChangeSeconds: csvUint(record, columns, "lastchg"),
		DowntimeSeconds:   csvUint(record, columns, "downtime"),
		Weight:            csvUint(record, columns, "weight"),
		Active:            csvUint(record, columns, "act") != 0,
		Backup:            csvUint(record, columns, "bck") != 0,
	}, nil
}

func csvField(record []string, columns map[string]int, name string) string {
	index, ok := columns[name]
	if !ok || index < 0 || index >= len(record) {
		return ""
	}
	return strings.TrimSpace(record[index])
}

func csvUint(record []string, columns map[string]int, name string) uint64 {
	value, err := strconv.ParseUint(csvField(record, columns, name), 10, 64)
	if err != nil {
		return 0
	}
	return value
}

func csvTrafficCounter(record []string, columns map[string]int, name string) (uint64, error) {
	raw := csvField(record, columns, name)
	value, err := strconv.ParseUint(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid HAProxy show stat %s counter", name)
	}
	return value, nil
}

func metricName(value string) (string, bool) {
	value = strings.TrimSpace(value)
	return value, value != "" && len(value) <= maxHAProxyMetricNameBytes && !strings.ContainsRune(value, '\x00')
}

func metricText(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > maxHAProxyMetricTextBytes {
		value = value[:maxHAProxyMetricTextBytes]
	}
	return value
}

func saturatingAdd(left, right uint64) uint64 {
	if math.MaxUint64-left < right {
		return math.MaxUint64
	}
	return left + right
}
