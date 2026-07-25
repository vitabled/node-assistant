package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
)

type HAProxyServiceAssignment struct {
	Generation int64 `json:"generation"`
	Enabled    bool  `json:"enabled"`
}

type HAProxyServiceSnapshot struct {
	State      string
	Generation int64
	LastError  string
}

type HAProxyServiceController struct {
	Runner      Runner
	Manager     *ConfigManager
	ServiceName string

	mu         sync.Mutex
	generation int64
	lastError  string
}

func (c *HAProxyServiceController) Snapshot(ctx context.Context) HAProxyServiceSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err := c.readState(ctx)
	if err != nil {
		return HAProxyServiceSnapshot{State: "unknown", Generation: c.generation, LastError: c.lastError}
	}
	return HAProxyServiceSnapshot{State: state, Generation: c.generation, LastError: c.lastError}
}

func (c *HAProxyServiceController) Reconcile(ctx context.Context, assignment HAProxyServiceAssignment) error {
	if assignment.Generation < 1 {
		return errors.New("invalid HAProxy service control generation")
	}
	if c.Runner == nil || c.Manager == nil || strings.TrimSpace(c.ServiceName) == "" {
		return errors.New("HAProxy service controller is not configured")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if assignment.Generation < c.generation {
		return nil
	}
	err := c.Manager.RunSerialized(func() error {
		action := "stop"
		if assignment.Enabled {
			action = "start"
		}
		if output, runErr := c.Runner.Run(ctx, "systemctl", action, c.ServiceName); runErr != nil {
			return fmt.Errorf("systemctl %s failed: %s", action, strings.TrimSpace(string(output)))
		}

		// Runtime state is authoritative for the operator action. Some HAProxy
		// packages ship both a native systemd unit and /etc/init.d/haproxy;
		// systemctl enable/disable then delegates to systemd-sysv-install and may
		// fail even though start/stop works. Keep boot persistence best-effort so
		// that a packaging compatibility error cannot block config reconciliation.
		c.reconcileBootState(ctx, assignment.Enabled)

		state, stateErr := c.readState(ctx)
		if stateErr != nil {
			return stateErr
		}
		if assignment.Enabled && state != "active" && state != "reloading" {
			return fmt.Errorf("HAProxy service did not become active: %s", state)
		}
		if !assignment.Enabled && state != "inactive" {
			return fmt.Errorf("HAProxy service did not stop: %s", state)
		}
		return nil
	})
	if err != nil {
		c.lastError = boundedServiceError(err.Error())
		return err
	}
	c.generation = assignment.Generation
	c.lastError = ""
	return nil
}

func (c *HAProxyServiceController) reconcileBootState(ctx context.Context, enabled bool) {
	output, err := c.Runner.Run(ctx, "systemctl", "is-enabled", c.ServiceName)
	current := strings.ToLower(strings.TrimSpace(string(output)))
	if err == nil {
		if enabled && (current == "enabled" || current == "static" || current == "indirect" || current == "generated") {
			return
		}
		if !enabled && (current == "disabled" || current == "masked") {
			return
		}
	}
	action := "disable"
	if enabled {
		action = "enable"
	}
	_, _ = c.Runner.Run(ctx, "systemctl", action, c.ServiceName)
}

func (c *HAProxyServiceController) readState(ctx context.Context) (string, error) {
	output, err := c.Runner.Run(ctx, "systemctl", "show", "--property=ActiveState", "--value", c.ServiceName)
	if err != nil {
		return "unknown", fmt.Errorf("read HAProxy service state: %w", err)
	}
	state := strings.ToLower(strings.TrimSpace(string(output)))
	switch state {
	case "active", "reloading", "inactive", "failed", "activating", "deactivating":
		return state, nil
	default:
		return "unknown", nil
	}
}

func boundedServiceError(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > 200 {
		return value[:200]
	}
	return value
}
