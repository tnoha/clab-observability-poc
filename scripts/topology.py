"""Single source of truth for cEOS and GoBGP external addressing."""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path

MANAGED_NAMES = ["core1", "core2", "edge1", "edge2"]
# endpoint, Ethernet index, endpoint, Ethernet index, link subnet index
INTERNAL_LINKS = [
    ("core1", 1, "core2", 1, 0),
    ("core1", 2, "edge1", 1, 1),
    ("core2", 2, "edge1", 2, 2),
    ("core1", 3, "edge2", 1, 3),
    ("core2", 3, "edge2", 2, 4),
]
EXTERNAL_COUNT = 16
EXTERNALS_PER_EDGE = 8
EXPECTED_BGP_SERIES = len(MANAGED_NAMES) * (len(MANAGED_NAMES) - 1) + EXTERNAL_COUNT
EXPECTED_ETHERNET_SERIES = len(INTERNAL_LINKS) * 2 + EXTERNAL_COUNT


@dataclass(frozen=True)
class External:
    name: str
    edge: str
    edge_interface: int
    subnet_index: int
    asn: int
    router_id: str
    link_address: str
    peer_address: str
    prefix_pool: str
    phase_slot: int


def external_specs():
    pools = list(IPv4Network("198.18.0.0/16").subnets(new_prefix=20))
    base = IPv4Address("10.0.0.0")
    result = []
    for offset in range(EXTERNAL_COUNT):
        slot = offset % EXTERNALS_PER_EDGE
        subnet_index = 5 + offset
        edge_address = base + subnet_index * 2
        result.append(
            External(
                name=f"external{offset + 1}",
                edge="edge1" if offset < EXTERNALS_PER_EDGE else "edge2",
                edge_interface=3 + slot,
                subnet_index=subnet_index,
                asn=65101 + offset,
                router_id=f"10.255.1.{offset + 1}",
                link_address=f"{edge_address + 1}/31",
                peer_address=str(edge_address + 1),
                prefix_pool=str(pools[offset]),
                phase_slot=slot,
            )
        )
    return result


EXTERNALS = external_specs()


def configs(root: Path, username: str, password: str):
    for n, name in enumerate(MANAGED_NAMES, 1):
        lines = [
            f"hostname {name}",
            "no aaa root",
            f"username {username} privilege 15 role network-admin secret 0 {password}",
            "service routing protocols model multi-agent",
            "ip routing",
            "management api gnmi",
            "   transport grpc default",
            "      port 6030",
            "management ssh",
            "   no shutdown",
            "interface Management0",
            f"   ip address 172.31.100.{10 + n}/24",
            "interface Loopback0",
            f"   ip address 10.255.0.{n}/32",
            "   ip ospf area 0.0.0.0",
        ]
        for a, ai, b, bi, subnet in INTERNAL_LINKS:
            if name not in (a, b):
                continue
            first = name == a
            peer, idx = (b, ai) if first else (a, bi)
            ip = subnet * 2 + (0 if first else 1)
            lines += [
                f"interface Ethernet{idx}",
                f"   description TO-{peer}",
                "   no switchport",
                f"   ip address 10.0.0.{ip}/31",
                "   no shutdown",
                "   ip ospf network point-to-point",
                "   ip ospf area 0.0.0.0",
            ]
        for external in (item for item in EXTERNALS if item.edge == name):
            edge_ip = IPv4Address(external.peer_address) - 1
            lines += [
                f"interface Ethernet{external.edge_interface}",
                f"   description TO-{external.name}",
                "   no switchport",
                f"   ip address {edge_ip}/31",
                "   no shutdown",
            ]
        lines += ["router ospf 1", f"   router-id 10.255.0.{n}", "   passive-interface Loopback0"]
        lines += ["router bgp 65000", f"   router-id 10.255.0.{n}", "   no bgp default ipv4-unicast"]
        peers = []
        for other in range(1, len(MANAGED_NAMES) + 1):
            if other == n:
                continue
            peer = f"10.255.0.{other}"
            peers.append(peer)
            lines += [f"   neighbor {peer} remote-as 65000", f"   neighbor {peer} update-source Loopback0"]
            if name.startswith("edge"):
                lines += [f"   neighbor {peer} next-hop-self"]
        for external in (item for item in EXTERNALS if item.edge == name):
            peers.append(external.peer_address)
            lines += [f"   neighbor {external.peer_address} remote-as {external.asn}"]
        lines += ["   address-family ipv4"] + [f"      neighbor {peer} activate" for peer in peers]
        path = root / "runtime" / "ceos" / f"{name}.cfg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)
