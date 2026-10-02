COLLECTOR := collector
GO        ?= go

.PHONY: api-test integration help test test-race fuzz vet fmt fixtures build up down logs gen replay status exporters

help:
	@echo "test       unit + fixture tests"
	@echo "test-race  tests with the race detector"
	@echo "fuzz       fuzz the sFlow decoder for 60s"
	@echo "fixtures   regenerate test fixtures and golden files"
	@echo "integration  tests against ClickHouse (Docker)"
	@echo "api-test   API unit + ClickHouse tests (Docker)"
	@echo "build      build collector and sflow-gen binaries into ./bin"
	@echo "up/down    start/stop the Docker Compose stack"
	@echo "gen        send synthetic sFlow to localhost:6343 (EXPORTERS, RATE)"
	@echo "replay     replay a capture: make replay FILE=capture.pcap"
	@echo "status     collector counters;  exporters: exporter inventory"

test:
	cd $(COLLECTOR) && $(GO) test -count=1 ./...

test-race:
	cd $(COLLECTOR) && $(GO) test -race -count=1 ./...

fuzz:
	cd $(COLLECTOR) && $(GO) test ./internal/decoder -run='^$$' -fuzz=FuzzDecode -fuzztime=60s

integration:
	scripts/integration-test.sh

api-test:
	scripts/api-test.sh

vet:
	cd $(COLLECTOR) && $(GO) vet ./...

fmt:
	cd $(COLLECTOR) && gofmt -w .

fixtures:
	cd $(COLLECTOR) && $(GO) run ./cmd/sflow-gen -write-fixtures tests/fixtures && $(GO) test ./tests -update

build:
	mkdir -p bin
	cd $(COLLECTOR) && CGO_ENABLED=0 $(GO) build -o ../bin/collector ./cmd/collector
	cd $(COLLECTOR) && CGO_ENABLED=0 $(GO) build -o ../bin/sflow-gen ./cmd/sflow-gen

up:
	scripts/dev-up.sh

down:
	docker compose down

logs:
	docker compose logs -f collector

EXPORTERS ?= 3
RATE      ?= 10
gen:
	cd $(COLLECTOR) && $(GO) run ./cmd/sflow-gen -target 127.0.0.1:6343 -exporters $(EXPORTERS) -rate $(RATE)

replay:
	cd $(COLLECTOR) && $(GO) run ./cmd/sflow-gen -target 127.0.0.1:6343 -replay $(abspath $(FILE)) -speed 1

status:
	@curl -s http://127.0.0.1:8000/api/v1/system/status

exporters:
	@curl -s http://127.0.0.1:8081/v1/exporters
