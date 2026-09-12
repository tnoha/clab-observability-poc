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

PROM = "http://127.0.0.1:9090"
SEARCH = "http://127.0.0.1:9200"


def query(expression):
    r = requests.get(PROM + "/api/v1/query", params={"query": expression}, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result["status"] != "success":
        raise RuntimeError(result)
    return result["data"]["result"]


def healthy_metrics():
    targets = {target for group in ecs.discover(ecs.client()) for target in group["targets"]}
    results = query("collector_connected")
    return (
        len(targets) == 1
        and len(results) == 6
        and {r["metric"]["device"] for r in results} == {d.name for d in inventory()}
        and all(r["metric"]["instance"] in targets and float(r["value"][1]) == 1 for r in results)
    )


def steady_metrics():
    if not healthy_metrics():
        return False
    states = query("network_bgp_session_up")
    interfaces = query('network_interface_oper_up{interface=~"Ethernet.*"}')
    counters = query('network_interface_in_octets_total{interface=~"Ethernet.*"}')
    return (
        len(states) == 16
        and len(interfaces) == 14
        and len(counters) == 14
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
    counts = [3, 3, 4, 4, 1, 1]
    for device, count in zip(inventory(), counts):
        with connection(device) as conn:
            r = conn.send_command("show ip bgp summary | json")
            r.raise_for_status()
            peers = json.loads(r.result)["vrfs"]["default"]["peers"]
            if len(peers) != count or any(p["peerState"] != "Established" for p in peers.values()):
                return False
    return True


def ping_check():
    for index, destination, source in [(4, "198.51.100.1", "192.0.2.1"), (5, "192.0.2.1", "198.51.100.1")]:
        with connection(inventory()[index]) as conn:
            result = conn.send_command(f"ping {destination} source {source} repeat 3")
            if "0% packet loss" not in result.result or "100% packet loss" in result.result:
                return False
    return True


def latest(transport):
    r = requests.post(
        SEARCH + "/observations-*/_search",
        json={
            "size": 200,
            "query": {"term": {"source.transport": transport}},
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


def evidence(name, data):
    (ROOT / f"runtime/evidence/{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def verify():
    from lab import wait_for

    started = datetime.now(timezone.utc).isoformat()
    wait_for("all 16 directed BGP sessions established", bgp_check)
    wait_for("external prefixes reachable in both directions", ping_check)
    wait_for("six gNMI streams", healthy_metrics)
    arn = ecs.collect_cli()
    wait_for("CLI/gNMI state agreement and fresh observations", compare, timeout=60)
    wait_for("current task has 16 BGP and 14 Ethernet metrics", steady_metrics, timeout=60)
    states = query("network_bgp_session_up")
    assert len(states) == 16 and all(float(s["value"][1]) == 1 for s in states)
    interfaces = query('network_interface_oper_up{interface=~"Ethernet.*"}')
    assert len(interfaces) == 14 and all(float(s["value"][1]) == 1 for s in interfaces)
    drops = query("collector_dropped_observations_total")
    assert drops and all(float(s["value"][1]) == 0 for s in drops), "Collector dropped observations"
    counters = query('network_interface_in_octets_total{interface=~"Ethernet.*"}')
    assert len(counters) == 14
    auth = (os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"])
    for uid in ("prometheus", "opensearch"):
        r = requests.get(f"http://127.0.0.1:3000/api/datasources/uid/{uid}/health", auth=auth, timeout=15)
        r.raise_for_status()
        assert r.json()["status"] == "OK", r.text
    r = requests.get("http://127.0.0.1:3000/api/dashboards/uid/network-poc", auth=auth, timeout=10)
    r.raise_for_status()
    from check_dashboard import check_dashboard

    check_dashboard()
    evidence(
        "verify",
        {
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "bgp_sessions": len(states),
            "ethernet_interfaces": len(interfaces),
            "cli_task": arn,
            "state_agreement": True,
            "grafana_datasources": "OK",
        },
    )
    print("PASS: networking, actual ECS CLI/gNMI tasks, observations, Prometheus and Grafana")


def configure(device, commands):
    with connection(device) as conn:
        result = conn.send_configs(commands)
        result.raise_for_status()


def metric_equals(expression, value):
    result = query(expression)
    return bool(result) and all(float(x["value"][1]) == value for x in result)


def fault_test():
    from lab import wait_for

    verify()
    device = inventory()[2]
    result = {}
    expression = 'network_bgp_session_up{device="edge1",peer="10.0.0.11"}'
    try:
        start = time.monotonic()
        configure(device, ["interface Ethernet3", "shutdown"])
        wait_for("eBGP down visible within 60 seconds", lambda: metric_equals(expression, 0), timeout=60)
        result["ebgp_down_seconds"] = round(time.monotonic() - start, 2)
        ecs.collect_cli()
        wait_for("CLI/gNMI agree during external link failure", compare, timeout=60)
    finally:
        configure(device, ["interface Ethernet3", "no shutdown"])
    start = time.monotonic()
    wait_for("eBGP recovery visible within 60 seconds", lambda: metric_equals(expression, 1), timeout=60)
    result["ebgp_recovery_seconds"] = round(time.monotonic() - start, 2)
    ecs.collect_cli()
    wait_for("CLI/gNMI agree after recovery", compare, timeout=60)
    try:
        configure(inventory()[0], ["interface Ethernet2", "shutdown"])
        wait_for(
            "internal link down observed",
            lambda: metric_equals('network_interface_oper_up{device="core1",interface="Ethernet2"}', 0),
            timeout=60,
        )
        wait_for("traffic reroutes over redundant core", ping_check, timeout=60)
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
        wait_for("old BGP series removed", lambda: not query(expression), timeout=60)
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
