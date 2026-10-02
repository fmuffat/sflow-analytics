package clickhouse

import (
	"strings"
	"testing"
)

func TestRenderAndSplitMigrations(t *testing.T) {
	b, err := migrationFS.ReadFile("migrations/001_schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	stmts := splitStatements(render(string(b), Config{Database: "sflow", RetentionDays: 30}))
	if len(stmts) != 7 {
		t.Fatalf("statements = %d, want 7 (database + 6 tables)", len(stmts))
	}
	for _, s := range stmts {
		if strings.Contains(s, "{{") || strings.HasSuffix(s, ";") || strings.HasPrefix(strings.TrimSpace(s), "--") {
			t.Errorf("bad statement: %q", s)
		}
	}
	if !strings.Contains(stmts[1], "CREATE TABLE IF NOT EXISTS sflow.flow_records") ||
		!strings.Contains(stmts[1], "INTERVAL 30 DAY") {
		t.Errorf("flow_records statement = %s", stmts[1])
	}
}

func TestOpenRejectsInvalidDatabaseName(t *testing.T) {
	for _, name := range []string{"", "sflow; DROP", "1abc", "a-b"} {
		if _, err := Open(Config{Addr: "127.0.0.1:9000", Database: name}); err == nil {
			t.Errorf("database %q accepted", name)
		}
	}
}
