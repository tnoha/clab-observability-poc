import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from scrapli.driver.core import EOSDriver
from .config import credentials, profile
from .schema import BgpNeighbor, Interface, observation, now

LOG = logging.getLogger(__name__)


def state(value):
    if value is None:
        return None
    return {
        "up": "up",
        "connected": "up",
        "down": "down",
        "notconnect": "down",
        "disabled": "down",
        "lowerlayerdown": "down",
        "adminDown": "down",
    }.get(value, "unknown")


def normalize(device, command_type, payload, timestamp=None):
    command = profile()["commands"][command_type]
    data = json.loads(payload) if isinstance(payload, str) else payload
    if command_type == "bgp":
        if "vrfs" not in data:
            raise ValueError("BGP JSON missing vrfs")
        for vrf, body in data["vrfs"].items():
            if "peers" not in body:
                raise ValueError(f"BGP JSON missing peers in {vrf}")
            for peer, values in body["peers"].items():
                yield observation(
                    device,
                    "ssh",
                    BgpNeighbor(
                        vrf=vrf,
                        peer=peer,
                        local_as=body.get("asn"),
                        remote_as=values.get("asn"),
                        session_state=values.get("peerState", "").lower() or None,
                        prefixes_received=values.get("prefixReceived"),
                    ),
                    timestamp,
                    command=command,
                )
    else:
        if "interfaces" not in data:
            raise ValueError("Interface JSON missing interfaces")
        for name, values in data["interfaces"].items():
            status = values.get("interfaceStatus")
            admin = None if status is None else ("down" if status == "disabled" else "up")
            counters = values.get("interfaceCounters", {})
            yield observation(
                device,
                "ssh",
                Interface(
                    interface=name,
                    admin_state=admin,
                    oper_state=state(values.get("lineProtocolStatus")),
                    description=values.get("description"),
                    mtu=values.get("mtu"),
                    in_octets=counters.get("inOctets"),
                    out_octets=counters.get("outOctets"),
                    in_errors=counters.get("totalInErrors"),
                    out_errors=counters.get("totalOutErrors"),
                ),
                timestamp,
                command=command,
            )


def collect_device(device, repository):
    username, password = credentials(device)
    with EOSDriver(
        host=device.host,
        port=device.ssh_port,
        auth_username=username,
        auth_password=password,
        auth_secondary=password,
        auth_strict_key=False,
        transport="paramiko",
        timeout_socket=10,
        timeout_transport=15,
        timeout_ops=20,
    ) as conn:
        count = 0
        for kind, command in profile()["commands"].items():
            result = conn.send_command(command)
            result.raise_for_status()
            observations = list(normalize(device, kind, result.result, now()))
            repository.write(observations)
            count += len(observations)
        LOG.info("collected device=%s observations=%d", device.name, count)
        return count


def run(devices, repository):
    failed = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(collect_device, d, repository): d for d in devices}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                failed += 1
                LOG.exception("CLI collection failed device=%s", futures[future].name)
    return 1 if failed else 0
