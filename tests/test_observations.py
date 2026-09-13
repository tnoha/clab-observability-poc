import json
import time
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from prometheus_client import CollectorRegistry, generate_latest
from observability.cli import normalize, run
from observability.config import inventory
from observability.gnmi import StateCache
from observability.metrics import Metrics
from observability.repository import Repository
from observability.ecs import task_ips, write_discovery

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[1]
DEVICE = inventory()[2]


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def populated():
    cache = StateCache(DEVICE)
    for name in ("gnmi-interfaces.json", "gnmi-bgp.json"):
        for notification in fixture(name)["notification"]:
            cache.apply(notification)
    cache.validate_sync()
    return cache


def test_real_eos_cli_and_gnmi_agree():
    cache = populated()
    cli = {}
    for kind in ("bgp", "interfaces"):
        for obs in normalize(DEVICE, kind, fixture(f"eos-4.34.0F-{kind}.json")):
            cli[obs.entity_key] = obs
    assert len(cache.snapshot()) == len(cli)
    for obs in cache.snapshot():
        other = cli[obs.entity_key]
        fields = (
            ("admin_state", "oper_state")
            if obs.observation_type == "interface"
            else ("session_state", "remote_as")
        )
        for field in fields:
            if getattr(obs.data, field) is not None:
                assert getattr(obs.data, field) == getattr(other.data, field)
        assert obs.source.transport != other.source.transport


def test_cli_missing_values_stay_missing():
    result = list(normalize(DEVICE, "interfaces", {"interfaces": {"Ethernet1": {}}}))[0]
    assert result.data.oper_state is None and result.data.in_octets is None
    with pytest.raises(ValueError):
        list(normalize(DEVICE, "bgp", "not JSON"))
    with pytest.raises(ValueError):
        list(normalize(DEVICE, "interfaces", {}))


def test_partial_update_and_delete():
    cache = populated()
    key = ("interface", "Ethernet3")
    original = cache.make(key)
    timestamp = int(time.time() * 1e9)
    base = cache.paths[key]
    updated = cache.apply(
        {
            "timestamp": timestamp,
            "prefix": "interfaces",
            "update": [{"path": "interface[name=Ethernet3]/state/oper-status", "val": "DOWN"}],
        }
    )[0]
    assert updated.data.oper_state == "down"
    assert updated.data.admin_state == original.data.admin_state
    assert updated.data.in_octets == original.data.in_octets
    leaf_deleted = cache.apply({"timestamp": timestamp + 1, "delete": [base + "/counters/in-octets"]})[0]
    assert leaf_deleted.data.in_octets is None
    deleted = cache.apply({"timestamp": timestamp + 2, "delete": [base.removesuffix("/state")]})[0]
    assert deleted.deleted and key not in cache.values


def test_out_of_order_updates_are_ignored():
    cache = populated()
    key = ("interface", "Ethernet3")
    assert (
        cache.apply({"timestamp": 1, "update": [{"path": cache.paths[key] + "/oper-status", "val": "DOWN"}]})
        == []
    )
    assert cache.make(key).data.oper_state == "up"


def test_parent_delete_removes_all_interfaces():
    cache = populated()
    deleted = cache.apply({"timestamp": int(time.time() * 1e9), "delete": ["interfaces"]})
    assert deleted and all(o.deleted for o in deleted)
    assert all(k[0] != "interface" for k in cache.values)


def test_missing_paths_fail_sync():
    with pytest.raises(ValueError, match="missing"):
        StateCache(DEVICE).validate_sync()


def test_disconnect_and_silence_remove_series():
    metrics = Metrics([DEVICE.name])
    registry = CollectorRegistry()
    registry.register(metrics)
    metrics.update(DEVICE.name, populated().snapshot())
    initial = generate_latest(registry)
    assert b"network_bgp_session_up{" in initial
    last_observed = next(
        line
        for line in initial.splitlines()
        if line.startswith(b'collector_last_observed_timestamp_seconds{device="edge1"}')
    )
    with patch("observability.metrics.time.time", return_value=time.time() + 40):
        output = generate_latest(registry)
        assert b"network_bgp_session_up{" not in output
        assert b'collector_connected{device="edge1"} 0.0' in output
    metrics.disconnected(DEVICE.name)
    output = generate_latest(registry)
    assert b"network_bgp_session_up{" not in output
    assert b'collector_connected{device="edge1"} 0.0' in output
    assert last_observed in output


def test_device_dashboard_is_filtered_and_repeats_entities():
    dashboard = json.loads((ROOT / "configs/grafana/dashboards/device.json").read_text())
    assert dashboard["uid"] == "network-device"
    variables = {item["name"]: item for item in dashboard["templating"]["list"]}
    assert set(variables) == {"device", "peer", "interface"}
    assert variables["device"]["current"]["value"] == "core1"
    assert not variables["device"]["multi"] and not variables["device"]["includeAll"]
    assert variables["peer"]["multi"] and variables["peer"]["includeAll"]
    assert variables["interface"]["multi"] and variables["interface"]["includeAll"]

    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    assert panels[4]["type"] == "row" and panels[4]["repeat"] == "peer"
    assert panels[5]["type"] == "stat" and 'device="$device"' in panels[5]["targets"][0]["expr"]
    assert 'peer="$peer"' in panels[5]["targets"][0]["expr"]
    assert panels[6]["type"] == "state-timeline"
    assert panels[11]["repeat"] == "interface" and panels[11]["maxPerRow"] == 2
    assert [target["legendFormat"] for target in panels[11]["targets"]] == ["RX", "TX"]
    assert all('device="$device"' in target["expr"] for target in panels[11]["targets"])
    assert all('interface="$interface"' in target["expr"] for target in panels[11]["targets"])
    for panel_id in (8, 9):
        query = panels[panel_id]["targets"][0]["query"]
        assert "`device.name` = '$device'" in query
        assert query.index("where") < query.index("sort") < query.index("dedup")


def test_one_failed_device_does_not_cancel_others():
    called = []

    def collect(device, repository):
        called.append(device.name)
        if device.name == "edge1":
            raise TimeoutError("unreachable")

    with patch("observability.cli.collect_device", side_effect=collect):
        assert run(inventory(), Mock()) == 1
    assert sorted(called) == sorted(d.name for d in inventory())


def test_bulk_retries_reuse_document_ids():
    response = Mock()
    response.json.side_effect = [{"errors": True}, {"errors": False}]
    with (
        patch("observability.repository.requests.post", return_value=response) as post,
        patch("observability.repository.time.sleep"),
    ):
        Repository().write(populated().snapshot())
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["data"] == post.call_args_list[1].kwargs["data"]


def test_bulk_failure_is_not_silently_accepted():
    response = Mock()
    response.json.return_value = {"errors": True}
    with (
        patch("observability.repository.requests.post", return_value=response),
        patch("observability.repository.time.sleep"),
    ):
        with pytest.raises(RuntimeError):
            Repository().write(populated().snapshot())


def test_task_ip_discovery_deduplicates():
    assert task_ips(
        {
            "containers": [{"networkInterfaces": [{"privateIpv4Address": "172.31.100.90"}]}],
            "attachments": [{"details": [{"name": "privateIPv4Address", "value": "172.31.100.90"}]}],
        }
    ) == ["172.31.100.90"]


def test_discovery_removes_stopped_task(tmp_path):
    path = tmp_path / "gnmi.json"
    path.write_text('[{"targets": ["old:9804"]}]')
    client = Mock()
    client.list_tasks.return_value = {"taskArns": []}
    write_discovery(client, path)
    assert json.loads(path.read_text()) == []


def test_floci_discovery_uses_ecs_task_dns_when_eni_absent():
    from observability.ecs import discover

    client = Mock()
    client.list_tasks.return_value = {
        "taskArns": ["arn:aws:ecs:us-east-1:000000000000:task/observability/abc123"]
    }
    client.describe_tasks.return_value = {
        "tasks": [
            {
                "taskArn": client.list_tasks.return_value["taskArns"][0],
                "lastStatus": "RUNNING",
                "containers": [{"name": "gnmi-collector", "networkBindings": [{"bindIP": "0.0.0.0"}]}],
            }
        ]
    }
    assert discover(client)[0]["targets"] == ["floci-obs-ecs-abc123-gnmi-collector:9804"]


def test_unknown_and_deleted_metrics_are_not_zero():
    from observability.schema import Interface, observation

    metrics = Metrics([DEVICE.name])
    registry = CollectorRegistry()
    registry.register(metrics)
    obs = observation(DEVICE, "gnmi", Interface(interface="Ethernet3", oper_state="unknown"))
    metrics.update(DEVICE.name, [obs])
    assert b"network_interface_oper_up{" not in generate_latest(registry)
    obs.data.oper_state = "up"
    metrics.update(DEVICE.name, [obs])
    assert b"network_interface_oper_up{" in generate_latest(registry)
    obs.deleted = True
    metrics.update(DEVICE.name, [obs])
    assert b"network_interface_oper_up{" not in generate_latest(registry)


def test_raw_stream_fixture_preserves_prefixes_and_leaf_lists():
    from google.protobuf.json_format import ParseDict
    from pygnmi.spec.v080.gnmi_pb2 import SubscribeResponse
    from observability.wire import decode

    cache = StateCache(DEVICE)
    synced = False
    for raw in fixture("gnmi-wire.json"):
        message = decode(ParseDict(raw, SubscribeResponse()))
        if "update" in message:
            cache.apply(message["update"])
        if message.get("sync_response"):
            cache.validate_sync()
            synced = True
    assert synced
    assert len(cache.snapshot()) == 9
    assert cache.make(("interface", "Ethernet3")).data.oper_state == "up"
    assert cache.make(("bgp_neighbor", "default", "10.0.0.11")).data.remote_as == 65101
    assert isinstance(cache.values[("bgp_neighbor", "default", "10.0.0.11")]["supported-capabilities"], list)


def test_timestamps_are_ordered_per_leaf_not_entity():
    cache = StateCache(DEVICE)
    base = "interfaces/interface[name=Ethernet1]/state/"
    cache.apply({"timestamp": 2000000000, "update": [{"path": base + "counters/in-octets", "val": 100}]})
    cache.apply({"timestamp": 1000000000, "update": [{"path": base + "admin-status", "val": "UP"}]})
    assert cache.make(("interface", "Ethernet1")).data.admin_state == "up"
    assert cache.make(("interface", "Ethernet1")).data.in_octets == 100


def test_stale_update_cannot_resurrect_deleted_entity():
    cache = populated()
    base = cache.paths[("interface", "Ethernet3")]
    timestamp = int(time.time() * 1e9)
    cache.apply({"timestamp": timestamp, "delete": [base]})
    assert (
        cache.apply({"timestamp": timestamp - 1000, "update": [{"path": base + "/oper-status", "val": "UP"}]})
        == []
    )


def test_readiness_rejects_old_task_samples(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    import verify

    results = [{"metric": {"device": d.name, "instance": "old:9804"}, "value": [0, "1"]} for d in inventory()]
    with (
        patch.object(verify.ecs, "client"),
        patch.object(verify.ecs, "discover", return_value=[{"targets": ["new:9804"]}]),
        patch.object(verify, "query", return_value=results),
    ):
        assert not verify.healthy_metrics()
        for result in results:
            result["metric"]["instance"] = "new:9804"
        assert verify.healthy_metrics()
        results.extend(
            {"metric": {"device": d.name, "instance": "old:9804"}, "value": [0, "1"]} for d in inventory()
        )
        assert verify.healthy_metrics()
