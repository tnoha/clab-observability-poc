---
name: observation-contract
description: Modify or review the shared Observation contract and its SSH, gNMI, OpenSearch, and Prometheus behavior in this repository. Use for schema fields, normalization, entity identity, partial updates, deletes, timestamp ordering, freshness, persistence retries, or CLI/gNMI parity.
---

# Observation contract

Keep the SSH and gNMI collection paths semantically consistent without inventing values that a
transport or EOS version did not provide.

## Read the contract before editing

Read the parts relevant to the requested change:

- `docs/design.md` for the intended semantics and EOS limitations.
- `src/observability/schema.py` for the Pydantic models and entity keys.
- `src/observability/cli.py` for CLI normalization.
- `src/observability/gnmi.py` for identity, partial state, timestamps, deletes, sync, and batching.
- `src/observability/wire.py` for raw protobuf and TypedValue decoding.
- `src/observability/repository.py` for index templates, serialization, IDs, and retries.
- `src/observability/metrics.py` for the live-state projection and freshness behavior.
- `tests/test_observations.py` and the real EOS fixtures for executable expectations.

Identify which surfaces the change affects: schema, CLI normalization, gNMI normalization,
OpenSearch mapping/history, Prometheus labels or values, dashboards, fixtures, tests, and docs.
Do not broaden a transport-specific fix into the shared contract without evidence that it applies
to both paths.

## Preserve the semantic invariants

- Interface identity is device ID plus interface name.
- BGP identity is device ID plus VRF, peer, and AFI/SAFI.
- Transport is source provenance and is excluded from `entity_key`; OpenSearch still stores
  observations from each transport as separate historical documents.
- `observed_at` is collector response time for CLI and device notification time for gNMI.
  `collected_at` is normalization time.
- `null` means unavailable or not observed. Never translate it to zero, `down`, or a healthy state.
- A partial gNMI notification updates only the included leaves and retains other known leaves.
- Compare timestamps per leaf because EOS initial sync may report different historical times for
  fields in one entity.
- Track deletion times so an older update cannot resurrect an entity. Entity deletion produces a
  historical `deleted=true` observation and removes it from live metrics.
- A collector must be synchronized and fresh before it exports network state. On disconnect or
  after 35 seconds without observations, remove network series but retain the last-observed time
  for age reporting.
- Do not silently accept OpenSearch bulk errors. Retries for one batch must reuse document IDs.
- A failure on one CLI device must not cancel collection from the other devices.

Respect measured cEOS 4.34.0F behavior: Loopbacks lack the OpenConfig operational leaves used for
Ethernet validation; OpenConfig MTU zero is treated as unavailable; gNMI neighbor state does not
supply local AS or prefix counts; and the v1 contract uses `ipv4-unicast`. Do not fill these gaps
from CLI or topology intent.

The direct raw `subscribe` path is deliberate. pyGNMI 0.8.15 `subscribe2` does not preserve this
lab's initial notification prefixes and EOS leaf-list values correctly. Any replacement requires
validation against `gnmi-wire.json`, `gnmi-stream.json`, and a live initial sync.

## Implement and validate changes

1. Make the smallest contract change that satisfies the request.
2. Add focused tests for missing values, deletes, out-of-order leaves, stale data, retry IDs, or
   cross-transport parity as applicable.
3. Use existing real EOS fixtures for deterministic tests. Do not run `scripts/probe.py` or
   `scripts/probe_stream.py` unless refreshing tracked fixtures is explicitly in scope; both
   overwrite files.
4. If the schema changes, decide explicitly whether `schema_version` and the OpenSearch index
   template require migration or compatibility handling.
5. If Prometheus names, labels, or query semantics change, update both dashboards through
   `scripts/dashboard.py`, regenerate both dashboard JSON files, and update dashboard tests.
6. Run `make test`, `uv run --frozen ruff check src scripts tests`, and
   `uv run --frozen ruff format --check src scripts tests`.
7. Use `make verify` only when live end-to-end validation is warranted and an appropriate lab is
   available. Fixture tests are not a substitute for a claimed live STREAM result, and a live run
   is not required merely to review the contract.

Update `docs/design.md` whenever the semantics or compatibility boundary changes. Update README
for user-visible collection or dashboard behavior. Add to `docs/validation.md` only after an actual
live validation run.
