# Repository guidance

## Purpose and boundaries

This repository is a local Containerlab observability proof of concept. It models a six-node
Arista cEOS network, collects state through SSH/CLI and gNMI STREAM, normalizes both paths into
one Observation schema, stores history in OpenSearch, exposes current state to Prometheus, and
visualizes the data in Grafana.

Floci emulates the ECS control plane while using the host Docker daemon. Do not present results
from this repository as validation of real AWS Fargate isolation, VPC networking, IAM,
availability, performance, or cost.

## Working conventions

- Run project commands from the repository root.
- Use Python 3.12 and the locked environment through `uv run --frozen ...`.
- Keep Python, container, network OS, and tool versions pinned unless the task explicitly covers
  an upgrade.
- Preserve unrelated working-tree changes. Never discard or overwrite them.
- Never expose or commit `.env`, generated startup configurations, runtime data, credentials, or
  credential-bearing logs.
- Treat `runtime/` and `clab-*` as generated local state.

## Sources of truth

- `src/observability/schema.py`: Observation contract and entity keys.
- `scripts/topology.py`: addressing and generated cEOS startup configurations. Do not hand-edit
  `runtime/ceos/`.
- `lab/inventory.yml`: managed devices.
- `lab/eos-profile.yml`: EOS commands and gNMI subscription paths.
- `lab/observability.clab.yml`: live lab infrastructure.
- `scripts/dashboard.py`: source for both provisioned dashboard JSON files under
  `configs/grafana/dashboards/`. Edit the generator, regenerate both files, and review their diffs.
- `docs/design.md`: schema semantics and version-specific compatibility boundaries.
- `docs/validation.md`: results of actual live validation, never expected or hypothetical results.

## Observation invariants

When changing collection, normalization, storage, or metrics, preserve these rules unless the
requested change explicitly revises the contract:

- SSH and gNMI use the same entity identity; transport is provenance, not identity.
- Missing or unsupported values remain `null`; do not infer zero, `down`, or another state.
- Deletions remain historical `deleted=true` observations and disappear from live metrics.
- gNMI partial updates retain independently known fields and are ordered per leaf.
- Stale updates cannot resurrect deleted entities.
- Disconnected, unsynchronized, or stale collectors do not publish old network state as current.
  The last-observed timestamp remains available for age reporting.
- OpenSearch retries reuse document IDs for the same batch.
- Preserve CLI/gNMI agreement only for fields supported by both EOS paths. Follow the exceptions
  documented in `docs/design.md` rather than synthesizing unavailable values.

The raw gNMI decode path in `src/observability/wire.py` and per-leaf timestamps in
`src/observability/gnmi.py` are deliberate pyGNMI 0.8.15 / cEOS 4.34.0F compatibility behavior.
Do not replace them with higher-level helpers without fixture and live STREAM validation.

## Validation

For ordinary Python or generated-dashboard changes, run the applicable local checks:

1. `make test`
2. `uv run --frozen ruff check src scripts tests`
3. `uv run --frozen ruff format --check src scripts tests`

For dashboard changes, edit `scripts/dashboard.py`, run
`uv run --frozen python scripts/dashboard.py`, and review both generated JSON files. If live
validation is in scope, run `uv run --frozen python scripts/check_dashboard.py --reload` against
an available lab.

Topology, image, infrastructure, and end-to-end collector changes require live validation when
the environment is available and the task calls for it. Use `make doctor`, then the relevant
`make build`, controlled `make down` / `make up`, and `make verify` sequence. `make up` does not
replace an already-running topology after image or topology changes. Do not claim live validation
unless the commands actually succeeded; machine-readable evidence belongs in `runtime/evidence/`.

## Stateful and destructive operations

Do not operate the live lab merely for code inspection or unit-level work.

- `make build` creates local credentials/runtime files and Docker images.
- `make up`, `make collect-cli`, and `make verify` create or update live state and observations.
- `make down` stops ECS tasks and destroys the topology while retaining persisted observations.
- `make fault-test` intentionally changes router state and interrupts collection.
- `scripts/probe.py` and `scripts/probe_stream.py` overwrite tracked fixtures.
- `make clean` destroys the topology and deletes all data under `runtime/`.

Never run `make clean` unless the user explicitly requests deletion. Run fault injection or
fixture capture only when the requested task requires it. If fault injection is interrupted,
verify restoration before reporting success; recreate the lab from generated startup configs if
restoration cannot be established.

## Documentation

- Update `docs/design.md` when schema semantics, compatibility boundaries, or lifecycle behavior
  change.
- Update `README.md` when setup, commands, endpoints, resource requirements, or user-visible
  behavior change.
- Update `docs/validation.md` only from an actual run, including environment and date when the
  validated compatibility set changes.
