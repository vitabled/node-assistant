package agent

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strconv"
	"strings"
)

// ObservedConfigState is sampled under the same lock as apply/rollback so a
// heartbeat can never combine a revision marker from one config with bytes
// from another.
type ObservedConfigState struct {
	ActualRevision *int64
	SHA256         string
}

func (m *ConfigManager) ObservedConfigState() ObservedConfigState {
	if m == nil {
		return ObservedConfigState{}
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	var observed ObservedConfigState
	if marker, err := m.actualRevision(); err == nil {
		if revision, parseErr := strconv.ParseInt(strings.TrimSpace(marker), 10, 64); parseErr == nil && revision > 0 {
			observed.ActualRevision = &revision
		}
	}
	if config, err := os.ReadFile(m.ManagedConfig); err == nil && len(config) > 0 {
		sum := sha256.Sum256(config)
		observed.SHA256 = hex.EncodeToString(sum[:])
	}
	return observed
}
