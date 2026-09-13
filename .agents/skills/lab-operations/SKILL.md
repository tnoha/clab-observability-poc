---
name: lab-operations
description: Operate or diagnose this repository's local Containerlab, Floci ECS, cEOS, OpenSearch, Prometheus, and Grafana lab. Use for build, startup, shutdown, live verification, fault injection, collector failures, container health, or end-to-end troubleshooting. Do not use for unit-test-only changes.
---

# Lab operations

Operate the local lab through the repository's Make targets and diagnostic scripts while
preserving generated data unless deletion is explicitly requested.

## Establish the current state

Before changing live state:

1. Read `README.md`, `Makefile`, and the relevant portions of `scripts/lab.py`.
2. Inspect `git status --short` and preserve unrelated edits.
3. Determine whether the task needs only static inspection, local tests, a healthy live lab, a
   topology/image replacement, fault injection, or data deletion.
4. Do not call `scripts/prepare.py` during read-only diagnosis: it creates `.env`, runtime
   directories, discovery state, and generated startup configurations.

The validated baseline is Linux amd64, Python 3.12, Containerlab 0.79.0, cEOS 4.34.0F, and Floci
2.0.1. The lab expects roughly 16 GiB available RAM, 20 GiB free disk, an unused
`172.31.100.0/24`, and a locally imported `ceos:4.34.0F` image.

## Choose the least disruptive workflow

- For prerequisites, use `make doctor`.
- For the first setup, follow the README: locked dependency sync, doctor, build, up, CLI
  collection, and verify.
- For collector image, Grafana image, or topology changes, use `make build`, then a controlled
  `make down` and `make up` before `make verify`. A running topology is intentionally left intact
  by `make up`, so `make up` alone does not deploy those changes.
- For live application behavior that does not require replacement, use the smallest relevant
  command and finish with `make verify` when end-to-end acceptance is required.
- `make down` retains OpenSearch, Prometheus, and Grafana data under `runtime/` but stops ECS tasks
  and destroys the Containerlab topology.
- `make clean` also deletes all contents of `runtime/`. Run it only when the user explicitly asks
  to delete lab data.

Announce resource-heavy or state-changing live operations before running them. A general request
to inspect, test, or verify code does not authorize `make clean`, fixture capture, or fault
injection.

## Diagnose failures

Use evidence from the failing layer rather than guessing:

- `uv run --frozen python scripts/ecs_status.py` reports ECS tasks, Prometheus targets, and BGP
  state. It contacts the live lab and loads local credentials.
- `uv run --frozen python scripts/compare_status.py` explains CLI/gNMI entity differences,
  freshness, drops, and collector errors without changing router state.
- `uv run --frozen python scripts/check_dashboard.py` checks Grafana datasource health and runs the provisioned OpenSearch queries, but it calls `prepare.py` and may create local state. Add `--reload` only when provisioning changes should be applied to the running Grafana instance.
- Use targeted Docker logs only for the relevant `clab-obs-*` or Floci task container. Avoid
  printing environment variables or credentials.
- Check `runtime/evidence/verify.json` and `runtime/evidence/fault-test.json` only as records of
  prior runs; they do not prove the current lab is healthy.

Keep the layers distinct: Containerlab owns fixed nodes and links, Floci owns collector task
containers, the discovery helper maps the current gNMI task into Prometheus file discovery,
OpenSearch stores history, and Prometheus exposes fresh live state.

## Fault injection and recovery

`make fault-test` shuts interfaces, removes and restores the edge1 gNMI transport, and stops an ECS
task to test replacement. Run it only when fault behavior is in scope and the lab begins healthy.

The test uses `finally` blocks for router restoration, but interruption or host failure can prevent
cleanup. After interruption, inspect the affected interfaces and gNMI transport. If restoration is
uncertain, use a controlled `make down` / `make up` to regenerate router state before reporting the
lab healthy. Finish a successful fault test with its built-in final verification and inspect the
written evidence.

## Report results

Distinguish among local tests, live verification, and live fault testing. Report the exact command
that failed and the first useful failing layer. Update `docs/validation.md` only with measurements
from a completed live run; never copy expected counts or earlier evidence into a new claim.
