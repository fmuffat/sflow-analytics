// Package httpapi exposes the collector's health, counters and exporter
// inventory over HTTP (JSON). It is an internal endpoint consumed by the
// future API service, not a public interface.
package httpapi

import (
	"encoding/json"
	"net/http"
	"time"

	"sflow-analytics/collector/internal/exporters"
	"sflow-analytics/collector/internal/metrics"
)

// Info is static information about the running collector.
type Info struct {
	Version       string `json:"version"`
	ListenAddress string `json:"listen_address"`
}

// Handler builds the HTTP routes. storage may be nil (no database configured);
// otherwise its result is included in /v1/status.
func Handler(info Info, m *metrics.Collector, reg *exporters.Registry, storage func() any) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	mux.HandleFunc("GET /v1/status", func(w http.ResponseWriter, _ *http.Request) {
		snap := m.Snapshot(time.Now())
		snap.ExportersTotal, snap.ExportersActive = reg.Counts()
		var st any
		if storage != nil {
			st = storage()
		}
		writeJSON(w, http.StatusOK, struct {
			Status string `json:"status"`
			Info
			Counters metrics.Snapshot `json:"counters"`
			Storage  any              `json:"storage"`
		}{"running", info, snap, st})
	})

	mux.HandleFunc("GET /v1/exporters", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, reg.Snapshot(r.URL.Query().Get("interfaces") == "true"))
	})

	mux.HandleFunc("GET /v1/exporters/{id...}", func(w http.ResponseWriter, r *http.Request) {
		e, ok := reg.Get(r.PathValue("id"))
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "exporter not found"})
			return
		}
		writeJSON(w, http.StatusOK, e)
	})

	return mux
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}
