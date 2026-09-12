"""Single source of truth for addressing and generated cEOS startup configuration."""

from pathlib import Path

NAMES = ["core1", "core2", "edge1", "edge2", "external1", "external2"]
# endpoint, Ethernet index, endpoint, Ethernet index, link subnet index
LINKS = [
    ("core1", 1, "core2", 1, 0),
    ("core1", 2, "edge1", 1, 1),
    ("core2", 2, "edge1", 2, 2),
    ("core1", 3, "edge2", 1, 3),
    ("core2", 3, "edge2", 2, 4),
    ("edge1", 3, "external1", 1, 5),
    ("edge2", 3, "external2", 1, 6),
]


def configs(root: Path, username: str, password: str):
    for n, name in enumerate(NAMES, 1):
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
        ]
        if n <= 4:
            lines += ["   ip ospf area 0.0.0.0"]
        for a, ai, b, bi, subnet in LINKS:
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
            ]
            if a in NAMES[:4] and b in NAMES[:4]:
                lines += ["   ip ospf network point-to-point", "   ip ospf area 0.0.0.0"]
        asn = 65000 if n <= 4 else 65100 + n - 4
        if n > 4:
            prefix = "192.0.2" if n == 5 else "198.51.100"
            lines += ["interface Loopback1", f"   ip address {prefix}.1/24"]
        if n <= 4:
            lines += ["router ospf 1", f"   router-id 10.255.0.{n}", "   passive-interface Loopback0"]
        lines += [f"router bgp {asn}", f"   router-id 10.255.0.{n}", "   no bgp default ipv4-unicast"]
        peers = []
        if n <= 4:
            for other in range(1, 5):
                if other != n:
                    p = f"10.255.0.{other}"
                    peers.append(p)
                    lines += [f"   neighbor {p} remote-as 65000", f"   neighbor {p} update-source Loopback0"]
                    if name.startswith("edge"):
                        lines += [f"   neighbor {p} next-hop-self"]
        ext = {
            "edge1": ("10.0.0.11", 65101),
            "edge2": ("10.0.0.13", 65102),
            "external1": ("10.0.0.10", 65000),
            "external2": ("10.0.0.12", 65000),
        }
        if name in ext:
            p, remote = ext[name]
            peers.append(p)
            lines += [f"   neighbor {p} remote-as {remote}"]
        lines += ["   address-family ipv4"] + [f"      neighbor {p} activate" for p in peers]
        if n > 4:
            lines += [f"      network {prefix}.0/24"]
        path = root / "runtime" / "ceos" / f"{name}.cfg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)
