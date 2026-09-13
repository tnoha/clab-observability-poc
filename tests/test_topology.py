import sys
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from topology import (  # noqa: E402
    EXTERNALS,
    EXTERNAL_COUNT,
    EXTERNALS_PER_EDGE,
    EXPECTED_BGP_SERIES,
    EXPECTED_ETHERNET_SERIES,
    MANAGED_NAMES,
    configs,
)


def test_external_specs_are_unique_balanced_and_disjoint():
    assert MANAGED_NAMES == ["core1", "core2", "edge1", "edge2"]
    assert len(EXTERNALS) == EXTERNAL_COUNT == 16
    assert EXPECTED_BGP_SERIES == 28
    assert EXPECTED_ETHERNET_SERIES == 26
    assert [sum(item.edge == edge for item in EXTERNALS) for edge in ("edge1", "edge2")] == [
        EXTERNALS_PER_EDGE,
        EXTERNALS_PER_EDGE,
    ]
    assert {item.asn for item in EXTERNALS} == set(range(65101, 65117))
    assert {item.peer_address for item in EXTERNALS} == {f"10.0.0.{value}" for value in range(11, 42, 2)}
    assert [item.phase_slot for item in EXTERNALS[:8]] == list(range(8))
    assert [item.phase_slot for item in EXTERNALS[8:]] == list(range(8))
    pools = [IPv4Network(item.prefix_pool) for item in EXTERNALS]
    assert all(pool.prefixlen == 20 and pool.num_addresses >= 1300 for pool in pools)
    assert all(not left.overlaps(right) for index, left in enumerate(pools) for right in pools[index + 1 :])


def test_containerlab_external_nodes_match_topology_specs():
    lab = yaml.safe_load((ROOT / "lab/observability.clab.yml").read_text())
    nodes = lab["topology"]["nodes"]
    links = lab["topology"]["links"]
    for item in EXTERNALS:
        node = nodes[item.name]
        assert node["kind"] == "linux"
        assert node["image"] == "clab-observability-gobgp-injector:4.5.0"
        assert node["env"]["LOCAL_AS"] == str(item.asn)
        assert node["env"]["LOCAL_ADDRESS"] == item.link_address
        assert node["env"]["PEER_ADDRESS"] == str(IPv4Address(item.peer_address) - 1)
        assert node["env"]["PREFIX_POOL"] == item.prefix_pool
        assert node["env"]["PHASE_SLOT"] == str(item.phase_slot)
        endpoints = {tuple(link["endpoints"]) for link in links}
        assert (f"{item.edge}:eth{item.edge_interface}", f"{item.name}:eth1") in endpoints


def test_generated_ceos_configs_have_eight_external_neighbors_per_edge(tmp_path):
    configs(tmp_path, "observer", "password")
    for name in MANAGED_NAMES:
        path = tmp_path / f"runtime/ceos/{name}.cfg"
        assert path.exists()
        body = path.read_text()
        expected = [item for item in EXTERNALS if item.edge == name]
        assert sum(f"neighbor {item.peer_address} remote-as {item.asn}" in body for item in expected) == len(
            expected
        )
    assert not (tmp_path / "runtime/ceos/external1.cfg").exists()
