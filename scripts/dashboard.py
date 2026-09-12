"""Regenerate the provisioned dashboard from its compact definition."""

import json
from pathlib import Path

PANELS = []


def prom(title, expr, x, y, w=12, h=7, kind="timeseries", unit="short", instant=False):
    panel = {
        "id": len(PANELS) + 1,
        "title": title,
        "type": kind,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [
            {
                "refId": "A",
                "expr": expr,
                "legendFormat": "{{device}} {{interface}} {{peer}}",
                "instant": instant,
                "range": not instant,
                "format": "table" if kind == "table" else "time_series",
            }
        ],
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 12}},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom"}},
    }
    if kind in ("stat", "state-timeline"):
        panel["fieldConfig"]["defaults"].update(
            {
                "min": 0,
                "max": 1,
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "DOWN", "color": "red"},
                            "1": {"text": "UP", "color": "green"},
                        },
                    }
                ],
            }
        )
    PANELS.append(panel)


def ppl(title, query, x, y, w=24, h=9):
    PANELS.append(
        {
            "id": len(PANELS) + 1,
            "title": title,
            "type": "table",
            "datasource": {"type": "grafana-opensearch-datasource", "uid": "opensearch"},
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "description": "Latest received observation per entity and transport in the selected time range. Check collected_at; CLI runs on demand.",
            "targets": [{"refId": "A", "queryType": "PPL", "format": "table", "query": query}],
            "options": {"showHeader": True, "cellHeight": "sm"},
            "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        }
    )


prom(
    "Collector streams · synchronized / fresh",
    "max by (device) (collector_connected)",
    0,
    0,
    8,
    5,
    "stat",
    instant=True,
)
prom(
    "BGP sessions · established / expected 16",
    "sum(network_bgp_session_up)",
    8,
    0,
    8,
    5,
    "stat",
    instant=True,
)
PANELS[-1]["fieldConfig"]["defaults"].pop("mappings", None)
PANELS[-1]["fieldConfig"]["defaults"]["max"] = 16
prom(
    "Last observation · age in seconds",
    "time() - collector_last_observed_timestamp_seconds",
    16,
    0,
    8,
    5,
    "stat",
    "s",
    True,
)
PANELS[-1]["fieldConfig"]["defaults"].pop("mappings", None)
PANELS[-1]["fieldConfig"]["defaults"].pop("max", None)
prom(
    "BGP session history",
    "max by (device, vrf, peer, afi_safi) (network_bgp_session_up)",
    0,
    5,
    12,
    9,
    "state-timeline",
)
prom(
    "Ethernet operational state",
    'max by (device, interface) (network_interface_oper_up{interface=~"Ethernet.*"})',
    12,
    5,
    12,
    9,
    "state-timeline",
)
prom(
    "Interface ingress",
    'max by (device, interface) (rate(network_interface_in_octets_total{interface=~"Ethernet.*"}[1m])) * 8',
    0,
    14,
    unit="bps",
)
prom(
    "Interface egress",
    'max by (device, interface) (rate(network_interface_out_octets_total{interface=~"Ethernet.*"}[1m])) * 8',
    12,
    14,
    unit="bps",
)
base = "source = `observations-*` | sort - collected_at | dedup entity_key, `source.transport` "
ppl(
    "BGP · latest CLI and gNMI observations",
    base
    + '| where observation_type = "bgp_neighbor" | fields `device.name`, `data.peer`, `source.transport`, `data.session_state`, `data.remote_as`, collected_at, deleted',
    0,
    21,
)
ppl(
    "Interfaces · latest CLI and gNMI observations",
    base
    + '| where observation_type = "interface" | fields `device.name`, `data.interface`, `source.transport`, `data.admin_state`, `data.oper_state`, collected_at, deleted',
    0,
    30,
)
prom("Collector errors", "collector_errors_total", 0, 39)
prom("Observations dropped before persistence", "collector_dropped_observations_total", 12, 39)
Path("configs/grafana/dashboards/network.json").write_text(
    json.dumps(
        {
            "uid": "network-poc",
            "title": "ISP Network Observability",
            "tags": ["containerlab", "eos", "poc"],
            "schemaVersion": 41,
            "version": 1,
            "refresh": "10s",
            "time": {"from": "now-15m", "to": "now"},
            "timezone": "browser",
            "editable": True,
            "panels": PANELS,
        },
        indent=2,
    )
    + "\n"
)
