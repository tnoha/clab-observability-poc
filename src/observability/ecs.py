"""ECS lifecycle and Prometheus discovery via the emulator API."""

import json
import logging
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import boto3
from botocore.config import Config

CLUSTER = "observability"
SERVICE = "gnmi-collector"
IMAGE = "clab-observability-collector:0.1.0"
LOG = logging.getLogger(__name__)


def client():
    return boto3.client(
        "ecs",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://172.31.100.20:4566"),
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )


def task_ips(task):
    addresses = set()
    for container in task.get("containers", []):
        for interface in container.get("networkInterfaces", []):
            if interface.get("privateIpv4Address"):
                addresses.add(interface["privateIpv4Address"])
    for attachment in task.get("attachments", []):
        for detail in attachment.get("details", []):
            if detail.get("name") == "privateIPv4Address":
                addresses.add(detail["value"])
    return sorted(addresses)


def discover(ecs):
    arns = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")["taskArns"]
    if not arns:
        return []
    tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
    targets = set()
    for task in tasks:
        if task.get("lastStatus") != "RUNNING":
            continue
        addresses = task_ips(task)
        if addresses:
            targets.update(f"{ip}:9804" for ip in addresses)
        else:
            # Floci 2.0.1 does not serialize task ENIs. Its names are derived from
            # the ECS task ID and the explicitly configured resource namespace.
            task_id = task["taskArn"].rsplit("/", 1)[-1]
            for container in task.get("containers", []):
                if container["name"] == SERVICE:
                    targets.add(f"floci-obs-ecs-{task_id}-{SERVICE}:9804")
    targets = sorted(targets)
    return [{"targets": targets, "labels": {"service": SERVICE}}] if targets else []


def atomic_write(path, payload):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".discovery-", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_discovery(ecs, path):
    payload = json.dumps(discover(ecs)) + "\n"
    path = Path(path)
    if path.exists() and path.read_text() == payload:
        return
    atomic_write(path, payload)


def discovery_loop():
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    ecs = client()
    path = Path(os.getenv("DISCOVERY_PATH", "/discovery/gnmi.json"))
    while not stop.is_set():
        try:
            write_discovery(ecs, path)
        except Exception:
            LOG.exception("ECS discovery failed; removing stale targets")
            atomic_write(path, "[]\n")
        stop.wait(5)


def service_ready(ecs, task_definition):
    arns = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")["taskArns"]
    if len(arns) != 1:
        return False
    tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
    return (
        len(tasks) == 1
        and tasks[0].get("lastStatus") == "RUNNING"
        and tasks[0].get("taskDefinitionArn") == task_definition
    )


def register(ecs, mode):
    env = {
        "LAB_USERNAME": os.environ["LAB_USERNAME"],
        "LAB_PASSWORD": os.environ["LAB_PASSWORD"],
        "OPENSEARCH_URL": "http://172.31.100.21:9200",
    }
    response = ecs.register_task_definition(
        family=f"{mode}-collector",
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        containerDefinitions=[
            {
                "name": f"{mode}-collector",
                "image": IMAGE,
                "essential": True,
                "command": [mode],
                "environment": [{"name": key, "value": value} for key, value in env.items()],
                "portMappings": [{"containerPort": 9804, "protocol": "tcp"}] if mode == "gnmi" else [],
            }
        ],
    )
    return response["taskDefinition"]["taskDefinitionArn"]


def bootstrap():
    ecs = client()
    ecs.create_cluster(clusterName=CLUSTER)
    definitions = {mode: register(ecs, mode) for mode in ("cli", "gnmi")}
    services = ecs.describe_services(cluster=CLUSTER, services=[SERVICE]).get("services", [])
    if services and services[0].get("status") != "INACTIVE":
        ecs.update_service(
            cluster=CLUSTER, service=SERVICE, taskDefinition=definitions["gnmi"], desiredCount=1
        )
    else:
        ecs.create_service(
            cluster=CLUSTER,
            serviceName=SERVICE,
            taskDefinition=definitions["gnmi"],
            desiredCount=1,
            launchType="FARGATE",
        )
    return definitions


def collect_cli(timeout=180):
    ecs = client()
    response = ecs.run_task(cluster=CLUSTER, taskDefinition="cli-collector", launchType="FARGATE", count=1)
    if response.get("failures") or not response.get("tasks"):
        raise RuntimeError(f"RunTask failed: {response.get('failures')}")
    arn = response["tasks"][0]["taskArn"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]
        if task.get("lastStatus") == "STOPPED":
            containers = task.get("containers", [])
            if not containers or any(c.get("exitCode") != 0 for c in containers):
                raise RuntimeError(f"CLI task failed: {arn}; inspect Docker logs")
            return arn
        time.sleep(2)
    ecs.stop_task(cluster=CLUSTER, task=arn, reason="CLI collection deadline exceeded")
    raise TimeoutError("CLI task exceeded deadline")


def shutdown():
    ecs = client()
    if not any(a.endswith("/" + CLUSTER) for a in ecs.list_clusters().get("clusterArns", [])):
        return
    services = ecs.describe_services(cluster=CLUSTER, services=[SERVICE]).get("services", [])
    if services and services[0].get("status") != "INACTIVE":
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
    arns = ecs.list_tasks(cluster=CLUSTER, desiredStatus="RUNNING").get("taskArns", [])
    for arn in arns:
        ecs.stop_task(cluster=CLUSTER, task=arn, reason="Lab shutdown")
    deadline = time.monotonic() + 45
    while arns and time.monotonic() < deadline:
        tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
        if all(t.get("lastStatus") == "STOPPED" for t in tasks):
            return
        time.sleep(1)
    if arns:
        raise TimeoutError("ECS tasks did not stop; refusing to destroy management network")
