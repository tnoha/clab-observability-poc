"""Capture real STREAM notifications through initial sync and one further update."""

import json
from pathlib import Path
from pygnmi.client import gNMIclient
from google.protobuf.json_format import MessageToDict
from prepare import prepare
from observability.config import inventory, credentials, profile
from observability.gnmi import StateCache
from observability.wire import decode

prepare()
d = inventory()[2]
u, p = credentials(d)
with gNMIclient(target=(d.host, d.gnmi_port), username=u, password=p, insecure=True) as client:
    subscription = {
        "mode": "stream",
        "encoding": "json",
        "subscription": [
            {"path": path, "mode": "sample", "sample_interval": 10_000_000_000}
            for path in profile()["subscriptions"]
        ],
    }
    stream = client.subscribe(subscribe=subscription, timeout=45)
    messages, wire = [], []
    synced = False
    cache = StateCache(d)
    try:
        for raw in stream:
            message = decode(raw)
            messages.append(message)
            wire.append(MessageToDict(raw))
            if "update" in message:
                cache.apply(message["update"])
            if synced:
                break
            if message.get("sync_response"):
                cache.validate_sync()
                synced = True
        Path("tests/fixtures/gnmi-stream.json").write_text(json.dumps(messages, indent=2) + "\n")
        Path("tests/fixtures/gnmi-wire.json").write_text(json.dumps(wire, indent=2) + "\n")
        print("Real STREAM validated:", len(messages), "notifications,", len(cache.snapshot()), "entities")
    finally:
        stream.cancel()
