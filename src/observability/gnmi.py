"""Merge gNMI notifications by entity; transport metadata never defines identity."""

import logging
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pygnmi.client import gNMIclient
from .config import credentials, profile
from .schema import Interface, BgpNeighbor, observation
from .wire import decode

LOG = logging.getLogger(__name__)
IF = re.compile(r"^interfaces/interface\[name=([^\]]+)\]/state(?:/(.*))?$")
BGP = re.compile(
    r"^network-instances/network-instance\[name=([^\]]+)\]/protocols/protocol\[[^/]+/bgp/neighbors/neighbor\[neighbor-address=([^\]]+)\]/state(?:/(.*))?$"
)
BGP_PREFIXES_RECEIVED = re.compile(
    r"^network-instances/network-instance\[name=([^\]]+)\]/protocols/protocol\[[^/]+/bgp/neighbors/neighbor\[neighbor-address=([^\]]+)\]/afi-safis/afi-safi\[afi-safi-name=([^\]]+)\]/state/prefixes/received$"
)


def clean_path(path):
    return re.sub(r"(^|/)[\w-]+:", r"\1", path or "").strip("/")


def flatten(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten(child, "/".join(filter(None, [prefix, key.split(":")[-1]])))
    else:
        yield prefix, value


def identity(path):
    path = clean_path(path)
    match = IF.match(path)
    if match:
        return ("interface", match[1]), match[2] or ""
    match = BGP.match(path)
    if match:
        return ("bgp_neighbor", match[1], match[2]), match[3] or ""
    match = BGP_PREFIXES_RECEIVED.match(path)
    if match and match[3].split(":")[-1] == "IPV4_UNICAST":
        return ("bgp_neighbor", match[1], match[2]), "prefixes-received"
    return None, None


def entity_path(key, path):
    if key[0] == "interface":
        return path.split("/state")[0] + "/state"
    neighbor = path.split("/afi-safis/")[0].split("/state")[0]
    return neighbor + "/state"


class StateCache:
    def __init__(self, device):
        self.device = device
        self.values = {}
        self.paths = {}
        self.timestamps = {}
        self.leaf_timestamps = {}
        self.leaf_paths = {}
        self.tombstones = {}

    def apply(self, notification):
        touched = set()
        deleted = []
        prefix = clean_path(notification.get("prefix"))
        timestamp = datetime.fromtimestamp(notification["timestamp"] / 1e9, tz=timezone.utc)
        for update in notification.get("update", []):
            path = "/".join(filter(None, [prefix, clean_path(update["path"])]))
            key, leaf = identity(path)
            if key is None:
                continue
            base = path.split("/state")[0] + "/state"
            accepted = False
            for name, value in flatten(update["val"], leaf):
                full_path = path if leaf == "prefixes-received" else base + "/" + name
                if any(
                    (full_path == dead or full_path.startswith(dead + "/")) and timestamp <= at
                    for dead, at in self.tombstones.items()
                ):
                    continue
                times = self.leaf_timestamps.setdefault(key, {})
                if name in times and timestamp < times[name]:
                    continue
                self.values.setdefault(key, {})[name] = value
                times[name] = timestamp
                self.leaf_paths.setdefault(key, {})[name] = full_path
                accepted = True
            if accepted:
                self.paths[key] = entity_path(key, path)
                self.timestamps[key] = max(self.leaf_timestamps[key].values())
                touched.add(key)
        for deleted_path in notification.get("delete", []):
            path = "/".join(filter(None, [prefix, clean_path(deleted_path)]))
            self.tombstones[path] = max(timestamp, self.tombstones.get(path, timestamp))
            for key in list(self.values):
                base = self.paths[key]
                previous = self.make(key)
                removed = False
                for leaf in list(self.values[key]):
                    full_path = self.leaf_paths[key][leaf]
                    if (full_path == path or full_path.startswith(path + "/")) and self.leaf_timestamps[key][
                        leaf
                    ] <= timestamp:
                        del self.values[key][leaf]
                        del self.leaf_timestamps[key][leaf]
                        del self.leaf_paths[key][leaf]
                        removed = True
                if removed:
                    if not self.values[key]:
                        previous.deleted = True
                        previous.observed_at = timestamp
                        deleted.append(previous)
                        del (
                            self.values[key],
                            self.paths[key],
                            self.timestamps[key],
                            self.leaf_timestamps[key],
                            self.leaf_paths[key],
                        )
                        touched.discard(key)
                    else:
                        self.timestamps[key] = max(timestamp, self.timestamps[key])
                        touched.add(key)
        return [self.make(key) for key in touched] + deleted

    def make(self, key):
        values = self.values[key]
        if key[0] == "interface":

            def status(name):
                value = values.get(name)
                if value is None:
                    return None
                return {
                    "UP": "up",
                    "DOWN": "down",
                    "LOWER_LAYER_DOWN": "down",
                    "DORMANT": "down",
                    "NOT_PRESENT": "down",
                }.get(value, "unknown")

            data = Interface(
                interface=key[1],
                admin_state=status("admin-status"),
                oper_state=status("oper-status"),
                description=values.get("description"),
                mtu=values.get("mtu") or None,
                in_octets=values.get("counters/in-octets"),
                out_octets=values.get("counters/out-octets"),
                in_errors=values.get("counters/in-errors"),
                out_errors=values.get("counters/out-errors"),
            )
        else:
            data = BgpNeighbor(
                vrf=key[1],
                peer=key[2],
                remote_as=values.get("peer-as"),
                local_as=values.get("local-as"),
                session_state=(values.get("session-state") or "").lower() or None,
                prefixes_received=values.get("prefixes-received"),
            )
        return observation(
            self.device, "gnmi", data, self.timestamps[key], path=self.paths[key], timestamp_origin="device"
        )

    def snapshot(self):
        return [self.make(k) for k in self.values]

    def validate_sync(self):
        observations = self.snapshot()
        interfaces = [o.data for o in observations if isinstance(o.data, Interface)]
        bgp = [o.data for o in observations if isinstance(o.data, BgpNeighbor)]
        if (
            not interfaces
            or not bgp
            or any(
                i.oper_state is None or i.admin_state is None
                for i in interfaces
                if i.interface.startswith("Ethernet")
            )
        ):
            raise ValueError("Required BGP/interface state paths are missing at sync")
        if any(b.session_state is None for b in bgp):
            raise ValueError("Required BGP session-state is missing at sync")
        if any(
            i.in_octets is None or i.out_octets is None
            for i in interfaces
            if i.interface.startswith("Ethernet")
        ):
            raise ValueError("Required Ethernet counter paths are missing at sync")


class Sink:
    def __init__(self, repository, metrics, stop):
        self.repository, self.metrics, self.stop = repository, metrics, stop
        self.queue = queue.Queue(maxsize=120)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def put(self, observations):
        if not observations:
            return
        try:
            self.queue.put_nowait(observations)
        except queue.Full:
            self.metrics.error("queue_full", len(observations))

    def run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                batch = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.repository.write(batch)
            except Exception:
                LOG.exception("OpenSearch write failed")
                self.metrics.error("storage", len(batch))
            finally:
                self.queue.task_done()


def state_signature(obs):
    data = obs.data
    if isinstance(data, Interface):
        return obs.deleted, data.admin_state, data.oper_state
    return obs.deleted, data.session_state, data.remote_as, data.prefixes_received


def worker(device, metrics, sink, stop):
    delay = 1
    while not stop.is_set():
        metrics.disconnected(device.name)
        cache = StateCache(device)
        synced = False
        persisted_at = 0.0
        persisted_states = {}
        username, password = credentials(device)
        try:
            with gNMIclient(
                target=(device.host, device.gnmi_port),
                username=username,
                password=password,
                insecure=True,
                gnmi_timeout=15,
            ) as client:
                # A watchdog closes a silent stream, unblocking the subscription reader.
                last_message = [time.monotonic()]
                finished = threading.Event()

                def watchdog():
                    while not finished.wait(1):
                        if stop.is_set() or time.monotonic() - last_message[0] > 35:
                            client.close()
                            return

                watcher = threading.Thread(target=watchdog, daemon=True)
                watcher.start()
                try:
                    subscription = {
                        "mode": "stream",
                        "encoding": "json",
                        "subscription": [
                            {"path": p, "mode": "sample", "sample_interval": 10_000_000_000}
                            for p in profile()["subscriptions"]
                        ],
                    }
                    stream = client.subscribe(subscribe=subscription)
                    try:
                        for raw in stream:
                            message = decode(raw)
                            if stop.is_set():
                                break
                            last_message[0] = time.monotonic()
                            if "update" in message:
                                changed = cache.apply(message["update"])
                                if synced:
                                    metrics.update(device.name, changed)
                                    transitions = [
                                        o
                                        for o in changed
                                        if persisted_states.get(o.entity_key) != state_signature(o)
                                    ]
                                    sink.put(transitions)
                                    for obs in transitions:
                                        persisted_states[obs.entity_key] = state_signature(obs)
                                    if time.monotonic() - persisted_at >= 10:
                                        sink.put(cache.snapshot())
                                        persisted_at = time.monotonic()
                            if message.get("sync_response"):
                                cache.validate_sync()
                                synced = True
                                delay = 1
                                initial = cache.snapshot()
                                metrics.update(device.name, initial)
                                sink.put(initial)
                                persisted_at = time.monotonic()
                                persisted_states = {o.entity_key: state_signature(o) for o in initial}
                                LOG.info("gNMI synced device=%s entities=%d", device.name, len(initial))
                    finally:
                        stream.cancel()

                finally:
                    finished.set()
                    watcher.join(timeout=2)
        except Exception:
            if not stop.is_set():
                metrics.error("subscription")
                LOG.exception("gNMI subscription failed device=%s", device.name)
        finally:
            metrics.disconnected(device.name)
        stop.wait(delay)
        delay = min(delay * 2, 15)
