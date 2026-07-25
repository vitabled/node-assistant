package panel

import (
	"embed"
	"io/fs"
	"net/http"
	"path"
	"strings"
)

// webAssets is populated from frontend/dist by Dockerfile.panel or
// scripts/build-panel.sh. The checked-in production bundle keeps local Go
// builds and backend tests self-contained.
//
//go:embed web_dist/*
var webAssets embed.FS

func embeddedWebHandler() http.Handler {
	root, err := fs.Sub(webAssets, "web_dist")
	if err != nil {
		panic(err)
	}
	files := http.FileServer(http.FS(root))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "method not allowed")
			return
		}
		requested := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
		if requested == "." || requested == "" {
			requested = "index.html"
		}
		if info, statErr := fs.Stat(root, requested); statErr == nil && !info.IsDir() {
			files.ServeHTTP(w, r)
			return
		}
		// Client-side routes receive the application shell. Asset-like missing
		// paths remain 404 so broken bundles do not get HTML with a 200 status.
		if strings.Contains(path.Base(requested), ".") {
			http.NotFound(w, r)
			return
		}
		r2 := r.Clone(r.Context())
		r2.URL.Path = "/"
		files.ServeHTTP(w, r2)
	})
}
