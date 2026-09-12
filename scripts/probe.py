"""Capture real EOS fixtures without running configuration or credentials."""

import json
from pathlib import Path
from prepare import prepare
from observability.config import inventory, credentials, profile
from scrapli.driver.core import EOSDriver
from pygnmi.client import gNMIclient

prepare()
device = inventory()[2]
username, password = credentials(device)
with EOSDriver(
    host=device.host,
    auth_username=username,
    auth_password=password,
    auth_strict_key=False,
    transport="paramiko",
    timeout_ops=20,
) as conn:
    for name, command in profile()["commands"].items():
        result = conn.send_command(command)
        result.raise_for_status()
        data = json.loads(result.result)
        Path(f"tests/fixtures/eos-4.34.0F-{name}.json").write_text(json.dumps(data, indent=2) + "\n")
        print(name, json.dumps(data)[:2200], flush=True)
with gNMIclient(
    target=(device.host, device.gnmi_port), username=username, password=password, insecure=True
) as client:
    print("capabilities", json.dumps(client.capabilities())[:1200], flush=True)
    for path in profile()["subscriptions"]:
        result = client.get(path=[path], encoding="json")
        Path("tests/fixtures/gnmi-" + ("interfaces" if "interfaces" in path else "bgp") + ".json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        print(path, json.dumps(result)[:2800], flush=True)
