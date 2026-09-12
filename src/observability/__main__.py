import argparse
import logging
import signal
import threading
from prometheus_client import CollectorRegistry, start_http_server
from .config import inventory
from .repository import Repository
from . import cli
from .gnmi import worker, Sink
from .metrics import Metrics


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["cli", "gnmi", "discovery"])
    args = parser.parse_args()
    if args.mode == "discovery":
        from .ecs import discovery_loop

        discovery_loop()
        return 0
    devices = inventory()
    repository = Repository()
    repository.initialize()
    if args.mode == "cli":
        return cli.run(devices, repository)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    metrics = Metrics([d.name for d in devices])
    registry = CollectorRegistry()
    registry.register(metrics)
    start_http_server(9804, registry=registry)
    sink = Sink(repository, metrics, stop)
    threads = [threading.Thread(target=worker, args=(d, metrics, sink, stop), daemon=True) for d in devices]
    for thread in threads:
        thread.start()
    stop.wait()
    for thread in threads:
        thread.join(timeout=5)
    sink.thread.join(timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
