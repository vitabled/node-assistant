package panel

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Config struct {
	PanelVersion          string
	ListenAddr            string
	DatabaseURL           string
	DatabaseMaxConns      int32
	AdminToken            string
	PublicURL             string
	AgentPublicURL        string
	AgentTLSListenAddr    string
	AgentTLSCertFile      string
	AgentTLSKeyFile       string
	AgentTLSClientCAFile  string
	AgentTLSIssuerKeyFile string
	AgentTLSServerName    string
	AgentReleaseDir       string
	UpdateSigningKeyFile  string
	RequireAgentMTLS      bool
}

func LoadConfig() (Config, error) {
	c := Config{
		ListenAddr:            envOr("PANEL_LISTEN_ADDR", ":8080"),
		DatabaseURL:           os.Getenv("DATABASE_URL"),
		AdminToken:            os.Getenv("PANEL_ADMIN_TOKEN"),
		PublicURL:             strings.TrimSpace(os.Getenv("PANEL_PUBLIC_URL")),
		AgentPublicURL:        strings.TrimSpace(os.Getenv("PANEL_AGENT_PUBLIC_URL")),
		AgentTLSListenAddr:    strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_LISTEN_ADDR")),
		AgentTLSCertFile:      strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_CERT_FILE")),
		AgentTLSKeyFile:       strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_KEY_FILE")),
		AgentTLSClientCAFile:  strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_CLIENT_CA_FILE")),
		AgentTLSIssuerKeyFile: strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_ISSUER_KEY_FILE")),
		AgentTLSServerName:    strings.TrimSpace(os.Getenv("PANEL_AGENT_TLS_SERVER_NAME")),
		AgentReleaseDir:       envOr("PANEL_AGENT_RELEASE_DIR", "/var/lib/nodeflow/releases"),
		UpdateSigningKeyFile:  strings.TrimSpace(os.Getenv("PANEL_UPDATE_SIGNING_KEY_FILE")),
		DatabaseMaxConns:      10,
		RequireAgentMTLS:      true,
	}
	if c.AgentPublicURL == "" {
		c.AgentPublicURL = c.PublicURL
	}
	if raw := strings.TrimSpace(os.Getenv("PANEL_REQUIRE_AGENT_MTLS")); raw != "" {
		value, err := strconv.ParseBool(raw)
		if err != nil {
			return Config{}, fmt.Errorf("PANEL_REQUIRE_AGENT_MTLS must be true or false")
		}
		c.RequireAgentMTLS = value
	}
	if raw := os.Getenv("DATABASE_MAX_CONNS"); raw != "" {
		n, err := strconv.ParseInt(raw, 10, 32)
		if err != nil || n < 1 {
			return Config{}, fmt.Errorf("DATABASE_MAX_CONNS must be a positive integer")
		}
		c.DatabaseMaxConns = int32(n)
	}
	if c.DatabaseURL == "" {
		return Config{}, fmt.Errorf("DATABASE_URL is required")
	}
	if len(c.AdminToken) < 32 {
		return Config{}, fmt.Errorf("PANEL_ADMIN_TOKEN must contain at least 32 characters")
	}
	if c.AdminToken == "replace-with-at-least-32-random-characters" {
		return Config{}, fmt.Errorf("PANEL_ADMIN_TOKEN must not use the example placeholder")
	}
	if err := validateAbsoluteHTTPURL("PANEL_PUBLIC_URL", c.PublicURL); err != nil {
		return Config{}, err
	}
	if err := validateAbsoluteHTTPURL("PANEL_AGENT_PUBLIC_URL", c.AgentPublicURL); err != nil {
		return Config{}, err
	}
	if c.AgentTLSListenAddr != "" {
		if c.AgentTLSCertFile == "" || c.AgentTLSKeyFile == "" || c.AgentTLSClientCAFile == "" {
			return Config{}, fmt.Errorf("Agent mTLS listener requires certificate, key and client CA files")
		}
	} else if c.RequireAgentMTLS {
		return Config{}, fmt.Errorf("PANEL_REQUIRE_AGENT_MTLS requires PANEL_AGENT_TLS_LISTEN_ADDR")
	}
	if c.AgentTLSIssuerKeyFile != "" && c.AgentTLSClientCAFile == "" {
		return Config{}, fmt.Errorf("PANEL_AGENT_TLS_ISSUER_KEY_FILE requires PANEL_AGENT_TLS_CLIENT_CA_FILE")
	}
	if c.RequireAgentMTLS {
		u, _ := url.Parse(c.AgentPublicURL)
		if u == nil || u.Scheme != "https" {
			return Config{}, fmt.Errorf("PANEL_REQUIRE_AGENT_MTLS requires an https PANEL_AGENT_PUBLIC_URL")
		}
		if c.AgentTLSIssuerKeyFile == "" {
			return Config{}, fmt.Errorf("PANEL_REQUIRE_AGENT_MTLS requires PANEL_AGENT_TLS_ISSUER_KEY_FILE for node bootstrap")
		}
	}
	if !validTLSServerName(c.AgentTLSServerName) {
		return Config{}, fmt.Errorf("PANEL_AGENT_TLS_SERVER_NAME must be an IP address or DNS name")
	}
	if c.UpdateSigningKeyFile != "" {
		if !filepath.IsAbs(c.AgentReleaseDir) || filepath.Clean(c.AgentReleaseDir) != c.AgentReleaseDir || c.AgentReleaseDir == string(filepath.Separator) {
			return Config{}, fmt.Errorf("PANEL_AGENT_RELEASE_DIR must be a clean absolute directory")
		}
		if !filepath.IsAbs(c.UpdateSigningKeyFile) || filepath.Clean(c.UpdateSigningKeyFile) != c.UpdateSigningKeyFile {
			return Config{}, fmt.Errorf("PANEL_UPDATE_SIGNING_KEY_FILE must be a clean absolute path")
		}
	}
	return c, nil
}

func validTLSServerName(value string) bool {
	if value == "" || net.ParseIP(value) != nil {
		return true
	}
	if len(value) > 253 || strings.HasPrefix(value, ".") || strings.HasSuffix(value, ".") {
		return false
	}
	for _, label := range strings.Split(value, ".") {
		if len(label) < 1 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, character := range label {
			if !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || character == '-') {
				return false
			}
		}
	}
	return true
}

func validateAbsoluteHTTPURL(name, value string) error {
	if value == "" {
		return nil
	}
	u, err := url.Parse(value)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return fmt.Errorf("%s must be an absolute http(s) URL without credentials", name)
	}
	return nil
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
