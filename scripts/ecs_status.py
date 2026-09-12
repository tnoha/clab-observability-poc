"""Print ECS diagnostics without task definitions or credential overrides."""

import requests
from prepare import prepare
from verify import connection
from observability.config import inventory


import json
import os
from observability import ecs

os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:4566"
c = ecs.client()
arns = c.list_tasks(cluster=ecs.CLUSTER)["taskArns"]
for t in c.describe_tasks(cluster=ecs.CLUSTER, tasks=arns)["tasks"]:
    print(json.dumps({k: t.get(k) for k in ["taskArn", "lastStatus", "attachments", "containers"]}, indent=2))

r = requests.get("http://127.0.0.1:9090/api/v1/targets", timeout=10)
print("PROMETHEUS", json.dumps(r.json()["data"]["activeTargets"], indent=2)[:3500])


prepare()
for d in inventory():
    with connection(d) as conn:
        r = conn.send_command("show ip bgp summary | json")
        print(
            d.name,
            {p: v.get("peerState") for p, v in json.loads(r.result)["vrfs"]["default"]["peers"].items()},
        )
