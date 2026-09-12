"""Explain transport comparison failures without retrieving credentials."""

from datetime import datetime
import time
from verify import latest, query

ssh, gnmi = latest("ssh"), latest("gnmi")
print(
    "counts", len(ssh), len(gnmi), "missing ssh", set(gnmi) - set(ssh), "missing gnmi", set(ssh) - set(gnmi)
)
for k, a in ssh.items():
    b = gnmi.get(k)
    if not b:
        continue
    fields = (
        ("session_state", "remote_as")
        if a["observation_type"] == "bgp_neighbor"
        else ("admin_state", "oper_state")
    )
    diffs = {f: (a["data"].get(f), b["data"].get(f)) for f in fields if a["data"].get(f) != b["data"].get(f)}
    age = time.time() - datetime.fromisoformat(b["collected_at"].replace("Z", "+00:00")).timestamp()
    if diffs or age > 35:
        print(k, "diff", diffs, "age", round(age, 1))
print("drops", query("collector_dropped_observations_total"))
print("errors", query("collector_errors_total"))
