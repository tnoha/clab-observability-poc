#!/usr/bin/env python3
"""Run from repository root via make. All AWS calls target the local emulator."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import time
import requests
from prepare import prepare, ROOT
from observability import ecs

EXTERNAL_IMAGE = "clab-observability-gobgp-injector:4.5.0"


def command(*args, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture)


def wait_for(label, check, timeout=180):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                print(f"OK {label}", flush=True)
                return value
        except Exception as exc:
            error = exc
        time.sleep(2)
    raise TimeoutError(f"{label}: {error or 'not ready'}")


def http_ok(url):
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return True


def doctor():
    for binary in ("docker", "containerlab"):
        if not shutil.which(binary):
            raise RuntimeError(f"{binary} is required")
    version = command("containerlab", "version", capture=True).stdout
    if "0.79.0" not in version:
        raise RuntimeError("Validated Containerlab version is 0.79.0")
    command("docker", "info", "--format", "{{.ServerVersion}}")
    command("docker", "image", "inspect", "ceos:4.34.0F", "--format", "{{.Id}}")
    if int(Path("/proc/sys/vm/max_map_count").read_text()) < 262144:
        print("vm.max_map_count is low; OpenSearch uses node.store.allow_mmap=false for this PoC.")
    print("Doctor passed. Budget ~16 GiB RAM / 20 GiB disk; verify 172.31.100.0/24 is available.")


def up():
    prepare()
    doctor()
    command("docker", "image", "inspect", ecs.IMAGE, "--format", "{{.Id}}")
    command("docker", "image", "inspect", EXTERNAL_IMAGE, "--format", "{{.Id}}")
    # Existing topology is left intact; bootstrap below is repeatable.
    names = command(
        "docker", "ps", "--filter", "name=clab-obs-", "--format", "{{.Names}}", capture=True
    ).stdout.split()
    if not names:
        command("containerlab", "deploy", "-t", "lab/observability.clab.yml")
    wait_for("OpenSearch", lambda: http_ok("http://127.0.0.1:9200/_cluster/health"))
    wait_for("OpenSearch Dashboards", lambda: http_ok("http://127.0.0.1:5601/api/status"))
    wait_for("Grafana", lambda: http_ok("http://127.0.0.1:3000/api/health"))
    from observability.repository import Repository

    Repository("http://127.0.0.1:9200").initialize()
    response = requests.post(
        "http://127.0.0.1:5601/api/saved_objects/index-pattern/observations",
        params={"overwrite": "true"},
        headers={"osd-xsrf": "true"},
        json={"attributes": {"title": "observations-*", "timeFieldName": "collected_at"}},
        timeout=10,
    )
    response.raise_for_status()
    # Grafana's two PPL tables compile date filters on each 10-second refresh.
    # Keep a bounded limit sized for this dashboard and acceptance queries.
    response = requests.put(
        "http://127.0.0.1:9200/_cluster/settings",
        json={"persistent": {"script.context.filter.max_compilations_rate": "300/5m"}},
        timeout=10,
    )
    response.raise_for_status()
    wait_for("Floci ECS", lambda: ecs.client().list_clusters() is not None)
    definitions = ecs.bootstrap()
    wait_for(
        "current gNMI task revision running", lambda: ecs.service_ready(ecs.client(), definitions["gnmi"])
    )
    wait_for("gNMI Task address", lambda: ecs.discover(ecs.client()))
    ecs.write_discovery(ecs.client(), ROOT / "runtime/discovery/gnmi.json")
    from verify import healthy_metrics

    wait_for("all four gNMI streams synchronized", healthy_metrics)
    print("Grafana: http://localhost:3000 (credentials: .env)", flush=True)
    print("OpenSearch Dashboards: http://localhost:5601", flush=True)


def down():
    # Only attempt ECS when Floci is running. An API failure is an error, not permission to orphan tasks.
    running = command(
        "docker", "ps", "--filter", "name=clab-obs-floci", "--format", "{{.Names}}", capture=True
    ).stdout
    if running.strip():
        ecs.shutdown()
    command("containerlab", "destroy", "-t", "lab/observability.clab.yml", "--cleanup")
    discovery = ROOT / "runtime/discovery/gnmi.json"
    if discovery.exists():
        ecs.atomic_write(discovery, "[]\n")


def main():
    os.chdir(ROOT)
    os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:4566"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["doctor", "build", "up", "collect-cli", "verify", "down", "clean", "fault-test"]
    )
    args = parser.parse_args()
    if args.action == "doctor":
        doctor()
    elif args.action == "build":
        prepare()
        command("docker", "build", "-t", ecs.IMAGE, ".")
        command("docker", "build", "-t", "clab-observability-grafana:0.1.0", "configs/grafana")
        command("docker", "build", "-t", EXTERNAL_IMAGE, "external")
    elif args.action == "up":
        up()
    elif args.action == "collect-cli":
        prepare()
        print(ecs.collect_cli())
    elif args.action in ("verify", "fault-test"):
        prepare()
        from verify import verify, fault_test

        (verify if args.action == "verify" else fault_test)()
    elif args.action == "down":
        down()
    elif args.action == "clean":
        # Explicit destructive command, restricted to this lab's generated directories.
        down()
        command(
            "docker",
            "run",
            "--rm",
            "--user",
            "0",
            "-v",
            f"{ROOT / 'runtime'}:/data",
            "--entrypoint",
            "python",
            ecs.IMAGE,
            "-c",
            'import pathlib,shutil; [shutil.rmtree(p) if p.is_dir() else p.unlink() for p in pathlib.Path("/data").iterdir()]',
        )


if __name__ == "__main__":
    main()
