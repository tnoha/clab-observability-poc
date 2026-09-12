import threading
import time
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily
from .schema import Interface


class Metrics:
    """Render only fresh observations; missing data never becomes a healthy zero."""

    def __init__(self, names):
        self.lock = threading.Lock()
        self.devices = {name: {} for name in names}
        self.last = {name: 0 for name in names}
        self.errors = {}
        self.dropped = 0

    def disconnected(self, device):
        with self.lock:
            self.devices[device] = {}
            self.last[device] = 0

    def update(self, device, observations):
        with self.lock:
            received = time.time()
            self.last[device] = received
            for observation in observations:
                if observation.deleted:
                    self.devices[device].pop(observation.entity_key, None)
                else:
                    self.devices[device][observation.entity_key] = (received, observation)

    def error(self, reason, dropped=0):
        with self.lock:
            self.errors[reason] = self.errors.get(reason, 0) + 1
            self.dropped += dropped

    def collect(self):
        connected = GaugeMetricFamily(
            "collector_connected", "Synchronized stream with recent data", labels=["device"]
        )
        last = GaugeMetricFamily(
            "collector_last_observed_timestamp_seconds", "Last received observation", labels=["device"]
        )
        bgp = GaugeMetricFamily(
            "network_bgp_session_up",
            "BGP Established",
            labels=["device", "vrf", "peer", "afi_safi", "source"],
        )
        oper = GaugeMetricFamily(
            "network_interface_oper_up",
            "Interface operational state",
            labels=["device", "interface", "source"],
        )
        admin = GaugeMetricFamily(
            "network_interface_admin_up",
            "Interface administrative state",
            labels=["device", "interface", "source"],
        )
        counters = {
            key: CounterMetricFamily(
                f"network_interface_{key}", key, labels=["device", "interface", "source"]
            )
            for key in ("in_octets", "out_octets", "in_errors", "out_errors")
        }
        errors = CounterMetricFamily("collector_errors", "Collection/storage failures", labels=["reason"])
        dropped = CounterMetricFamily("collector_dropped_observations", "Observations not persisted")
        with self.lock:
            current = time.time()
            for device, entities in self.devices.items():
                fresh = self.last[device] > current - 35
                connected.add_metric([device], int(fresh))
                if self.last[device]:
                    last.add_metric([device], self.last[device])
                for received, obs in entities.values():
                    if not fresh or received < current - 35:
                        continue
                    data = obs.data
                    if isinstance(data, Interface):
                        labels = [device, data.interface, "gnmi"]
                        for metric, value in ((oper, data.oper_state), (admin, data.admin_state)):
                            if value in ("up", "down"):
                                metric.add_metric(labels, int(value == "up"))
                        for key, metric in counters.items():
                            value = getattr(data, key)
                            if value is not None:
                                metric.add_metric(labels, value)
                    elif data.session_state is not None:
                        bgp.add_metric(
                            [device, data.vrf, data.peer, data.afi_safi, "gnmi"],
                            int(data.session_state == "established"),
                        )
            for reason, count in self.errors.items():
                errors.add_metric([reason], count)
            dropped.add_metric([], self.dropped)
        yield from [connected, last, bgp, oper, admin, *counters.values(), errors, dropped]
