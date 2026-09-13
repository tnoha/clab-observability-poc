"""Acceptance checks against live containers, never fixture substitutes."""

import json
import os
import time
from datetime import datetime, timezone
import requests
from scrapli.driver.core import EOSDriver
from observability.config import inventory, credentials
from observability import ecs
from prepare import ROOT
from topology import EXTERNALS, EXPECTED_BGP_SERIES, EXPECTED_ETHERNET_SERIES

PROM = "http://127.0.0.1:9090"
SEARCH = "http://127.0.0.1:9200"


def query(expression):
    r = requests.get(PROM + "/api/v1/query", params={"query": expression}, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result["status"] != "success":
        raise RuntimeError(result)
    return result["data"]["result"]


def current_query(expression):
    targets = {target for group in ecs.discover(ecs.client()) for target in group["targets"]}
    return [result for result in query(expression) if result["metric"].get("instance") in targets]


def healthy_metrics():
    targets = {target for group in ecs.discover(ecs.client()) for target in group["targets"]}
    results = [
        result for result in query("collector_connected") if result["metric"].get("instance") in targets
    ]
    return (
        len(targets) == 1
        and len(results) == len(inventory())
        and {r["metric"]["device"] for r in results} == {d.name for d in inventory()}
        and all(r["metric"]["instance"] in targets and float(r["value"][1]) == 1 for r in results)
    )


def steady_metrics():
    if not healthy_metrics():
        return False
    states = current_query("network_bgp_session_up")
    prefixes = current_query("network_bgp_prefixes_received")
    interfaces = current_query('network_interface_oper_up{interface=~"Ethernet.*"}')
    counters = current_query('network_interface_in_octets_total{interface=~"Ethernet.*"}')
    return (
        len(states) == EXPECTED_BGP_SERIES
        and len(prefixes) == EXPECTED_BGP_SERIES
        and len(interfaces) == EXPECTED_ETHERNET_SERIES
        and len(counters) == EXPECTED_ETHERNET_SERIES
        and all(float(s["value"][1]) == 1 for s in states + interfaces)
    )


def connection(device):
    user, password = credentials(device)
    return EOSDriver(
        host=device.host,
        auth_username=user,
        auth_password=password,
        auth_secondary=password,
        auth_strict_key=False,
        transport="paramiko",
        timeout_ops=20,
        timeout_socket=10,
        timeout_transport=15,
    )


def bgp_check():
    counts = {"core1": 3, "core2": 3, "edge1": 11, "edge2": 11}
    for device in inventory():
        with connection(device) as conn:
            r = conn.send_command("show ip bgp summary | json")
            r.raise_for_status()
            peers = json.loads(r.result)["vrfs"]["default"]["peers"]
            if len(peers) != counts[device.name] or any(
                peer["peerState"] != "Established" for peer in peers.values()
            ):
                return False
    return True


def external_route_check():
    devices = {device.name: device for device in inventory()}
    for edge in ("edge1", "edge2"):
        expected = {item.peer_address: item for item in EXTERNALS if item.edge == edge}
        with connection(devices[edge]) as conn:
            summary = conn.send_command("show ip bgp summary | json")
            summary.raise_for_status()
            peers = json.loads(summary.result)["vrfs"]["default"]["peers"]
            if any(
                peer not in peers
                or peers[peer]["peerState"] != "Established"
                or not 700 <= peers[peer]["prefixReceived"] <= 1300
                for peer in expected
            ):
                return False
            for external in expected.values():
                sentinel = external.prefix_pool.split("/")[0] + "/32"
                result = conn.send_command(f"show ip bgp {sentinel}")
                if sentinel not in result.result:
                    return False
    return True


def internal_reachability():
    with connection(inventory()[0]) as conn:
        result = conn.send_command("ping 10.255.0.3 source 10.255.0.1 repeat 3")
        return "0% packet loss" in result.result and "100% packet loss" not in result.result


def external_prefix_metrics():
    expected = {(item.edge, item.peer_address) for item in EXTERNALS}
    return {
        (result["metric"].get("device"), result["metric"].get("peer")): float(result["value"][1])
        for result in current_query("network_bgp_prefixes_received")
        if (result["metric"].get("device"), result["metric"].get("peer")) in expected
    }


def gnmi_external_prefixes_in_range():
    values = external_prefix_metrics()
    return len(values) == len(EXTERNALS) and all(700 <= value <= 1300 for value in values.values())


def latest(transport):
    managed = [device.name for device in inventory()]
    r = requests.post(
        SEARCH + "/observations-*/_search",
        json={
            "size": 200,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"source.transport": transport}},
                        {"terms": {"device.name": managed}},
                    ]
                }
            },
            "sort": [{"collected_at": "desc"}],
            "collapse": {"field": "entity_key"},
        },
        timeout=15,
    )
    r.raise_for_status()
    return {hit["_source"]["entity_key"]: hit["_source"] for hit in r.json()["hits"]["hits"]}


def compare():
    ssh, gnmi = latest("ssh"), latest("gnmi")
    expected_devices = {d.name for d in inventory()}
    if {o["device"]["name"] for o in ssh.values()} != expected_devices or set(ssh) != set(gnmi):
        return False
    for key, cli in ssh.items():
        telemetry = gnmi[key]
        if cli["deleted"] or telemetry["deleted"]:
            return False
        age = (
            time.time() - datetime.fromisoformat(telemetry["collected_at"].replace("Z", "+00:00")).timestamp()
        )
        if age > 35:
            return False
        if cli["observation_type"] == "bgp_neighbor":
            fields = ("session_state", "remote_as")
        elif cli["data"]["interface"].startswith("Loopback"):
            continue  # EOS 4.34.0F OpenConfig omits Loopback operational state.
        else:
            fields = ("admin_state", "oper_state")
        if any(cli["data"].get(f) != telemetry["data"].get(f) for f in fields):
            return False
    return True


def ssh_external_prefixes_in_range():
    expected = {(item.edge, item.peer_address) for item in EXTERNALS}
    observations = [
        observation
        for observation in latest("ssh").values()
        if (observation["device"]["name"], observation["data"].get("peer")) in expected
    ]
    return len(observations) == len(EXTERNALS) and all(
        700 <= observation["data"].get("prefixes_received", -1) <= 1300 for observation in observations
    )


def evidence(name, data):
    (ROOT / f"runtime/evidence/{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def verify():
    from lab import wait_for

    started = datetime.now(timezone.utc).isoformat()
    wait_for(f"all {EXPECTED_BGP_SERIES} observed BGP sessions established", bgp_check)
    wait_for("all external peers advertise their sentinel and 700-1300 prefixes", external_route_check)
    wait_for("four gNMI streams", healthy_metrics)
    arn = ecs.collect_cli()
    wait_for("CLI/gNMI stable-field agreement and fresh observations", compare, timeout=60)
    wait_for("CLI external prefix counts are within the oscillator range", ssh_external_prefixes_in_range)
    wait_for(
        f"current task has {EXPECTED_BGP_SERIES} BGP and {EXPECTED_ETHERNET_SERIES} Ethernet metrics",
        steady_metrics,
        timeout=60,
    )
    wait_for("gNMI external prefix counts are within the oscillator range", gnmi_external_prefixes_in_range)
    initial_prefixes = external_prefix_metrics()

    def prefixes_changed_while_established():
        current = external_prefix_metrics()
        states = current_query("network_bgp_session_up")
        return (
            current.keys() == initial_prefixes.keys()
            and any(current[key] != initial_prefixes[key] for key in current)
            and len(states) == EXPECTED_BGP_SERIES
            and all(float(state["value"][1]) == 1 for state in states)
        )

    wait_for(
        "external prefix wave changes while all BGP sessions stay established",
        prefixes_changed_while_established,
        60,
    )
    states = current_query("network_bgp_session_up")
    assert len(states) == EXPECTED_BGP_SERIES and all(float(s["value"][1]) == 1 for s in states)
    prefixes = current_query("network_bgp_prefixes_received")
    assert len(prefixes) == EXPECTED_BGP_SERIES and all(float(p["value"][1]) >= 0 for p in prefixes)
    interfaces = current_query('network_interface_oper_up{interface=~"Ethernet.*"}')
    assert len(interfaces) == EXPECTED_ETHERNET_SERIES and all(
        float(state["value"][1]) == 1 for state in interfaces
    )
    drops = query("collector_dropped_observations_total")
    assert drops and all(float(s["value"][1]) == 0 for s in drops), "Collector dropped observations"
    counters = current_query('network_interface_in_octets_total{interface=~"Ethernet.*"}')
    assert len(counters) == EXPECTED_ETHERNET_SERIES
    auth = (os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"])
    for uid in ("prometheus", "opensearch"):
        r = requests.get(f"http://127.0.0.1:3000/api/datasources/uid/{uid}/health", auth=auth, timeout=15)
        r.raise_for_status()
        assert r.json()["status"] == "OK", r.text
    r = requests.get("http://127.0.0.1:5601/api/status", timeout=15)
    r.raise_for_status()
    assert r.json()["status"]["overall"]["state"] == "green", r.text
    r = requests.get("http://127.0.0.1:5601/api/saved_objects/index-pattern/observations", timeout=15)
    r.raise_for_status()
    assert r.json()["attributes"] == {
        "title": "observations-*",
        "timeFieldName": "collected_at",
    }, r.text
    for uid in ("network-poc", "network-device"):
        r = requests.get(f"http://127.0.0.1:3000/api/dashboards/uid/{uid}", auth=auth, timeout=10)
        r.raise_for_status()
    from check_dashboard import check_dashboard

    check_dashboard()
    evidence(
        "verify",
        {
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "bgp_sessions": len(states),
            "bgp_received_prefix_series": len(prefixes),
            "external_prefix_wave": "700-1300, change observed",
            "ethernet_interfaces": len(interfaces),
            "cli_task": arn,
            "state_agreement": True,
            "grafana_datasources": "OK",
            "opensearch_dashboards": "green",
            "opensearch_dashboards_index_pattern": "observations-*",
        },
    )
    print(
        "PASS: networking, actual ECS CLI/gNMI tasks, observations, Prometheus, "
        "Grafana and OpenSearch Dashboards"
    )


def configure(device, commands):
    with connection(device) as conn:
        result = conn.send_configs(commands)
        result.raise_for_status()


def metric_equals(expression, value):
    result = current_query(expression)
    return bool(result) and all(float(x["value"][1]) == value for x in result)


def fault_test():
    from lab import wait_for

    verify()
    device = inventory()[2]
    result = {}
    expression = 'network_bgp_session_up{device="edge1",peer="10.0.0.11"}'
    prefix_expression = 'network_bgp_prefixes_received{device="edge1",peer="10.0.0.11"}'
    try:
        start = time.monotonic()
        configure(device, ["interface Ethernet3", "shutdown"])
        wait_for("eBGP down visible within 60 seconds", lambda: metric_equals(expression, 0), timeout=60)
        result["ebgp_down_seconds"] = round(time.monotonic() - start, 2)
        ecs.collect_cli()
        wait_for(
            "external prefix count disappears or reaches zero",
            lambda: not current_query(prefix_expression) or metric_equals(prefix_expression, 0),
            timeout=60,
        )
        wait_for("CLI/gNMI stable fields agree during external link failure", compare, timeout=60)
    finally:
        configure(device, ["interface Ethernet3", "no shutdown"])
    start = time.monotonic()
    wait_for("eBGP recovery visible within 60 seconds", lambda: metric_equals(expression, 1), timeout=60)
    wait_for("external prefixes recover into oscillator range", gnmi_external_prefixes_in_range, timeout=60)
    result["ebgp_recovery_seconds"] = round(time.monotonic() - start, 2)
    ecs.collect_cli()
    wait_for("CLI/gNMI stable fields agree after recovery", compare, timeout=60)
    try:
        configure(inventory()[0], ["interface Ethernet2", "shutdown"])
        wait_for(
            "internal link down observed",
            lambda: metric_equals('network_interface_oper_up{device="core1",interface="Ethernet2"}', 0),
            timeout=60,
        )
        wait_for("traffic reroutes over redundant core", internal_reachability, timeout=60)
        result["internal_link_reroute"] = True
    finally:
        configure(inventory()[0], ["interface Ethernet2", "no shutdown"])
    try:
        configure(device, ["management api gnmi", "no transport grpc default"])
        wait_for(
            "disconnected stream reported unhealthy",
            lambda: metric_equals('collector_connected{device="edge1"}', 0),
            timeout=60,
        )
        wait_for("old BGP series removed", lambda: not current_query(expression), timeout=60)
        result["stale_series_removed"] = True
    finally:
        configure(device, ["management api gnmi", "transport grpc default", "port 6030"])
    wait_for("gNMI reconnect and initial sync", healthy_metrics, timeout=60)
    client = ecs.client()
    old = client.list_tasks(cluster=ecs.CLUSTER, serviceName=ecs.SERVICE, desiredStatus="RUNNING")["taskArns"]
    assert len(old) == 1
    client.stop_task(cluster=ecs.CLUSTER, task=old[0], reason="PoC recovery test")

    def replaced():
        arns = client.list_tasks(cluster=ecs.CLUSTER, serviceName=ecs.SERVICE, desiredStatus="RUNNING")[
            "taskArns"
        ]
        return len(arns) == 1 and arns[0] not in old

    wait_for("ECS Service replaces stopped task", replaced, timeout=90)
    wait_for("discovery tracks replacement and all streams recover", healthy_metrics, timeout=90)
    result["ecs_replacement"] = True
    evidence("fault-test", {**result, "final_verification": "pending"})
    verify()
    result["final_verification"] = "passed"
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    evidence("fault-test", result)
    print("PASS: link failures, reroute, stale series removal, reconnect and ECS task replacement")
