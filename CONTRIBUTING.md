# Contributing

Bug reports and suggestions are welcome as GitHub issues. The project is not open source (see
[LICENSE](LICENSE)): by submitting a pull request, a patch or any other contribution, you agree
that the copyright holder may use, modify and distribute it as part of sFlow Analytics, under
any license, without compensation.

## Development setup

Only Docker (with the Compose plugin) is required:

```bash
scripts/dev-up.sh --test     # stack + synthetic sFlow generator
open https://localhost/      # self-signed certificate
```

## Tests

| Component | Command | Needs |
|-----------|---------|-------|
| Collector | `make test` (`make test-race`, `make fuzz`) | Go 1.25 |
| Collector ↔ ClickHouse | `scripts/integration-test.sh` | Docker |
| API | `scripts/api-test.sh` | Docker |
| Web UI | `cd frontend && npm ci && npm run typecheck && npm test` | Node 22 |
| Pages render without console errors | `scripts/ui-screenshots.sh` | Docker, running stack |

CI (`.github/workflows/ci.yml`) runs all of them on every pull request.

## Rules

- Traffic figures derived from samples are **estimates**: label them as such.
- Every dashboard number comes from the API; every API figure from stored or collector data (no mock data).
- The collector must never crash on malformed telemetry; add a fixture and a test for each decoding case.
- All user input reaching ClickHouse is bound as a query parameter.
- Keep secrets and site-specific data (addresses, captures) out of the repository; use `docs/local/` (ignored).
- Update `CHANGELOG.md` and `TODO.md` with each change.
