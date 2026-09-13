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


def row(title, y, repeat=None):
    panel = {
        "id": len(PANELS) + 1,
        "title": title,
        "type": "row",
        "collapsed": False,
        "panels": [],
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
    }
    if repeat:
        panel["repeat"] = repeat
    PANELS.append(panel)


def variable(name, query, *, current, hide=0, multi=False, include_all=False):
    return {
        "name": name,
        "label": name.capitalize(),
        "type": "query",
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "definition": query,
        "query": {"query": query, "refId": f"variable-{name}"},
        "refresh": 1,
        "sort": 1,
        "hide": hide,
        "multi": multi,
        "includeAll": include_all,
        "current": current,
        "options": [],
    }


PANELS = []
row("Device health", 0)
prom(
    "Collector stream · synchronized / fresh",
    'max(collector_connected{device="$device"})',
    0,
    1,
    12,
    5,
    "stat",
    instant=True,
)
prom(
    "Last observation · age in seconds",
    'time() - max(collector_last_observed_timestamp_seconds{device="$device"})',
    12,
    1,
    12,
    5,
    "stat",
    "s",
    True,
)
PANELS[-1]["fieldConfig"]["defaults"].pop("mappings", None)
PANELS[-1]["fieldConfig"]["defaults"].pop("max", None)

row("BGP neighbors", 6)
prom(
    "Peer / Current state",
    'min by (peer) (network_bgp_session_up{device="$device"})',
    0,
    7,
    6,
    10,
    "stat",
    instant=True,
)
PANELS[-1]["targets"][0]["legendFormat"] = "{{peer}}"
PANELS[-1]["options"].update(
    {
        "colorMode": "background",
        "graphMode": "none",
        "justifyMode": "auto",
        "orientation": "horizontal",
        "textMode": "value_and_name",
        "text": {"titleSize": 14, "valueSize": 18},
        "wideLayout": True,
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
    }
)
prom(
    "State history",
    'min by (peer) (network_bgp_session_up{device="$device"})',
    6,
    7,
    18,
    10,
    "state-timeline",
)
PANELS[-1]["targets"][0]["legendFormat"] = "{{peer}}"

row("Latest observations", 17)
device_base = "source = `observations-*` | where `device.name` = '$device' "
ppl(
    "BGP · latest CLI and gNMI observations",
    device_base
    + '| where observation_type = "bgp_neighbor" | sort - collected_at | dedup entity_key, `source.transport` | fields `data.peer`, `source.transport`, `data.session_state`, `data.remote_as`, collected_at, deleted',
    0,
    18,
)
ppl(
    "Interfaces · latest CLI and gNMI observations",
    device_base
    + '| where observation_type = "interface" | sort - collected_at | dedup entity_key, `source.transport` | fields `data.interface`, `source.transport`, `data.admin_state`, `data.oper_state`, collected_at, deleted',
    0,
    27,
)

row("Interface traffic", 36)
prom(
    "Interface traffic · $interface",
    'max(rate(network_interface_in_octets_total{device="$device",interface=~"$interface"}[1m])) * 8',
    0,
    37,
    12,
    7,
    unit="bps",
)
PANELS[-1]["targets"][0]["legendFormat"] = "RX"
PANELS[-1]["targets"].append(
    {
        "refId": "B",
        "expr": 'max(rate(network_interface_out_octets_total{device="$device",interface=~"$interface"}[1m])) * 8',
        "legendFormat": "TX",
        "instant": False,
        "range": True,
        "format": "time_series",
    }
)
PANELS[-1].update({"repeat": "interface", "repeatDirection": "h", "maxPerRow": 2})

variables = [
    variable(
        "device",
        "label_values(collector_connected, device)",
        current={"selected": True, "text": "core1", "value": "core1"},
    ),
    variable(
        "interface",
        'label_values(network_interface_in_octets_total{device="$device",interface=~"Ethernet.*"}, interface)',
        current={"selected": True, "text": "All", "value": "$__all"},
        hide=2,
        multi=True,
        include_all=True,
    ),
]
Path("configs/grafana/dashboards/device.json").write_text(
    json.dumps(
        {
            "uid": "network-device",
            "title": "ISP Device Observability",
            "tags": ["containerlab", "eos", "poc"],
            "schemaVersion": 41,
            "version": 1,
            "refresh": "10s",
            "time": {"from": "now-15m", "to": "now"},
            "timezone": "browser",
            "editable": True,
            "panels": PANELS,
            "templating": {"list": variables},
        },
        indent=2,
    )
    + "\n"
)
