"""Regenerate the provisioned dashboards from their compact definitions."""

import json
from pathlib import Path


PROMETHEUS = {"type": "prometheus", "uid": "prometheus"}
OPENSEARCH = {"type": "grafana-opensearch-datasource", "uid": "opensearch"}
STATE_MAPPING = [
    {
        "type": "value",
        "options": {
            "0": {"text": "DOWN", "color": "red"},
            "1": {"text": "UP", "color": "green"},
        },
    }
]


def prom_panel(panel_id, title, expr, x, y, w=12, h=7, kind="timeseries", unit="short", instant=False):
    panel = {
        "id": panel_id,
        "title": title,
        "type": kind,
        "datasource": PROMETHEUS,
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
        panel["fieldConfig"]["defaults"].update({"min": 0, "max": 1, "mappings": STATE_MAPPING})
    return panel


def ppl_panel(panel_id, title, query, x, y, w=24, h=9):
    return {
        "id": panel_id,
        "title": title,
        "type": "table",
        "datasource": OPENSEARCH,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "description": (
            "Latest received observation per entity and transport in the selected time range. "
            "Check collected_at; CLI runs on demand."
        ),
        "targets": [{"refId": "A", "queryType": "PPL", "format": "table", "query": query}],
        "options": {"showHeader": True, "cellHeight": "sm"},
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
    }


def row(panel_id, title, y, repeat=None):
    panel = {
        "id": panel_id,
        "title": title,
        "type": "row",
        "collapsed": False,
        "panels": [],
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
    }
    if repeat:
        panel["repeat"] = repeat
    return panel


def dashboard(uid, title, panels, templating=None):
    result = {
        "uid": uid,
        "title": title,
        "tags": ["containerlab", "eos", "poc"],
        "schemaVersion": 41,
        "version": 1,
        "refresh": "10s",
        "time": {"from": "now-15m", "to": "now"},
        "timezone": "browser",
        "editable": True,
        "panels": panels,
    }
    if templating:
        result["templating"] = {"list": templating}
    return result


def overview_dashboard():
    panels = []
    panels.append(
        prom_panel(
            1,
            "Collector streams · synchronized / fresh",
            "max by (device) (collector_connected)",
            0,
            0,
            8,
            5,
            "stat",
            instant=True,
        )
    )
    panels.append(
        prom_panel(
            2,
            "BGP sessions · established / expected 16",
            "sum(network_bgp_session_up)",
            8,
            0,
            8,
            5,
            "stat",
            instant=True,
        )
    )
    panels[-1]["fieldConfig"]["defaults"].pop("mappings")
    panels[-1]["fieldConfig"]["defaults"]["max"] = 16
    panels.append(
        prom_panel(
            3,
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
    )
    panels[-1]["fieldConfig"]["defaults"].pop("mappings")
    panels[-1]["fieldConfig"]["defaults"].pop("max")
    panels.append(
        prom_panel(
            4,
            "BGP session history",
            "max by (device, vrf, peer, afi_safi) (network_bgp_session_up)",
            0,
            5,
            12,
            9,
            "state-timeline",
        )
    )
    panels.append(
        prom_panel(
            5,
            "Ethernet operational state",
            'max by (device, interface) (network_interface_oper_up{interface=~"Ethernet.*"})',
            12,
            5,
            12,
            9,
            "state-timeline",
        )
    )
    panels.append(
        prom_panel(
            6,
            "Interface ingress",
            'max by (device, interface) (rate(network_interface_in_octets_total{interface=~"Ethernet.*"}[1m])) * 8',
            0,
            14,
            unit="bps",
        )
    )
    panels.append(
        prom_panel(
            7,
            "Interface egress",
            'max by (device, interface) (rate(network_interface_out_octets_total{interface=~"Ethernet.*"}[1m])) * 8',
            12,
            14,
            unit="bps",
        )
    )
    base = "source = `observations-*` | sort - collected_at | dedup entity_key, `source.transport` "
    panels.append(
        ppl_panel(
            8,
            "BGP · latest CLI and gNMI observations",
            base
            + '| where observation_type = "bgp_neighbor" | fields `device.name`, `data.peer`, `source.transport`, `data.session_state`, `data.remote_as`, collected_at, deleted',
            0,
            21,
        )
    )
    panels.append(
        ppl_panel(
            9,
            "Interfaces · latest CLI and gNMI observations",
            base
            + '| where observation_type = "interface" | fields `device.name`, `data.interface`, `source.transport`, `data.admin_state`, `data.oper_state`, collected_at, deleted',
            0,
            30,
        )
    )
    panels.append(prom_panel(10, "Collector errors", "collector_errors_total", 0, 39))
    panels.append(
        prom_panel(
            11, "Observations dropped before persistence", "collector_dropped_observations_total", 12, 39
        )
    )
    return dashboard("network-poc", "ISP Network Observability", panels)


def variable(name, query, *, current, hide=0, multi=False, include_all=False):
    return {
        "name": name,
        "label": name.capitalize(),
        "type": "query",
        "datasource": PROMETHEUS,
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


def device_dashboard():
    device = 'device="$device"'
    peer = 'peer="$peer"'
    interface = 'interface="$interface"'
    panels = [row(1, "Device health", 0)]
    panels.append(
        prom_panel(
            2,
            "Collector stream · synchronized / fresh",
            f"max(collector_connected{{{device}}})",
            0,
            1,
            12,
            5,
            "stat",
            instant=True,
        )
    )
    panels.append(
        prom_panel(
            3,
            "Last observation · age in seconds",
            f"time() - max(collector_last_observed_timestamp_seconds{{{device}}})",
            12,
            1,
            12,
            5,
            "stat",
            "s",
            True,
        )
    )
    panels[-1]["fieldConfig"]["defaults"].pop("mappings")
    panels[-1]["fieldConfig"]["defaults"].pop("max")

    panels.append(row(4, "BGP neighbor · $peer", 6, repeat="peer"))
    panels.append(
        prom_panel(
            5,
            "Current state · $peer",
            f"min(network_bgp_session_up{{{device},{peer}}})",
            0,
            7,
            6,
            7,
            "stat",
            instant=True,
        )
    )
    panels.append(
        prom_panel(
            6,
            "State history · $peer",
            f"min by (peer) (network_bgp_session_up{{{device},{peer}}})",
            6,
            7,
            18,
            7,
            "state-timeline",
        )
    )

    panels.append(row(7, "Latest observations", 14))
    base = "source = `observations-*` | where `device.name` = '$device' "
    panels.append(
        ppl_panel(
            8,
            "BGP · latest CLI and gNMI observations",
            base
            + '| where observation_type = "bgp_neighbor" | sort - collected_at | dedup entity_key, `source.transport` | fields `data.peer`, `source.transport`, `data.session_state`, `data.remote_as`, collected_at, deleted',
            0,
            15,
        )
    )
    panels.append(
        ppl_panel(
            9,
            "Interfaces · latest CLI and gNMI observations",
            base
            + '| where observation_type = "interface" | sort - collected_at | dedup entity_key, `source.transport` | fields `data.interface`, `source.transport`, `data.admin_state`, `data.oper_state`, collected_at, deleted',
            0,
            24,
        )
    )

    panels.append(row(10, "Interface traffic", 33))
    traffic = prom_panel(
        11,
        "Interface traffic · $interface",
        f"max(rate(network_interface_in_octets_total{{{device},{interface}}}[1m])) * 8",
        0,
        34,
        12,
        7,
        unit="bps",
    )
    traffic["targets"][0]["legendFormat"] = "RX"
    traffic["targets"].append(
        {
            "refId": "B",
            "expr": f"max(rate(network_interface_out_octets_total{{{device},{interface}}}[1m])) * 8",
            "legendFormat": "TX",
            "instant": False,
            "range": True,
            "format": "time_series",
        }
    )
    traffic.update({"repeat": "interface", "repeatDirection": "h", "maxPerRow": 2})
    panels.append(traffic)

    variables = [
        variable(
            "device",
            "label_values(collector_connected, device)",
            current={"selected": True, "text": "core1", "value": "core1"},
        ),
        variable(
            "peer",
            'label_values(network_bgp_session_up{device="$device"}, peer)',
            current={"selected": True, "text": "All", "value": "$__all"},
            hide=2,
            multi=True,
            include_all=True,
        ),
        variable(
            "interface",
            'label_values(network_interface_in_octets_total{device="$device"}, interface)',
            current={"selected": True, "text": "All", "value": "$__all"},
            hide=2,
            multi=True,
            include_all=True,
        ),
    ]
    return dashboard("network-device", "ISP Device Observability", panels, variables)


def write_dashboards():
    target = Path("configs/grafana/dashboards")
    for filename, data in (("network.json", overview_dashboard()), ("device.json", device_dashboard())):
        (target / filename).write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    write_dashboards()
