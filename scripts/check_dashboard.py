"""Execute provisioned queries through Grafana's datasource API."""

import copy
import json
import os
import time
from pathlib import Path
import requests
from prepare import prepare


def reload_provisioning():
    prepare()
    auth = (os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"])
    for resource in ("datasources", "dashboards"):
        r = requests.post(
            f"http://127.0.0.1:3000/api/admin/provisioning/{resource}/reload", auth=auth, timeout=30
        )
        r.raise_for_status()


def check_dashboard():
    prepare()
    auth = (os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"])
    for uid in ("prometheus", "opensearch"):
        response = requests.get(
            f"http://127.0.0.1:3000/api/datasources/uid/{uid}/health", auth=auth, timeout=15
        )
        print(uid, response.status_code, response.text[:1500], flush=True)
    for filename in ("network.json", "device.json"):
        dashboard = json.loads(Path("configs/grafana/dashboards", filename).read_text())
        for panel in dashboard["panels"]:
            if panel.get("datasource", {}).get("uid") != "opensearch":
                continue
            target = copy.deepcopy(panel["targets"][0])
            target["query"] = target["query"].replace("$device", "core1")
            target["datasource"] = panel["datasource"]
            target["intervalMs"] = 10000
            target["maxDataPoints"] = 1000
            response = requests.post(
                "http://127.0.0.1:3000/api/ds/query",
                auth=auth,
                json={
                    "from": str(int((time.time() - 900) * 1000)),
                    "to": str(int(time.time() * 1000)),
                    "queries": [target],
                },
                timeout=30,
            )
            result = response.json()
            if response.status_code != 200:
                raise RuntimeError(f"{panel['title']}: {response.status_code}: {result}")
            data = result["results"]["A"]
            if data.get("error"):
                raise RuntimeError(f"{panel['title']}: {data['error']}")
            frames = data.get("frames", [])
            if not any(f.get("data", {}).get("values") and len(f["data"]["values"][0]) for f in frames):
                raise RuntimeError(f"{panel['title']}: no rows: {data}")
            print("OK Grafana query:", dashboard["title"], "·", panel["title"], flush=True)


if __name__ == "__main__":
    import sys

    if "--reload" in sys.argv:
        reload_provisioning()
    check_dashboard()
