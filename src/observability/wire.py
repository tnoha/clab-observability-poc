"""Decode raw pyGNMI Subscribe responses without merging notification prefixes.

pyGNMI 0.8.15 subscribe2 coalesces initial notifications under the last prefix
and its leaf-list parser fails on EOS supported-capabilities. The raw Subscribe
RPC keeps each notification intact; this small adapter preserves that boundary.
"""

import json
from pygnmi.client import gnmi_path_degenerator


def typed_value(value):
    kind = value.WhichOneof("value")
    if kind in ("json_val", "json_ietf_val"):
        return json.loads(getattr(value, kind))
    if kind == "leaflist_val":
        return [typed_value(element) for element in value.leaflist_val.element]
    if kind in ("string_val", "int_val", "uint_val", "bool_val", "float_val", "double_val", "ascii_val"):
        return getattr(value, kind)
    raise ValueError(f"Unsupported gNMI value type: {kind}")


def decode(response):
    kind = response.WhichOneof("response")
    if kind == "sync_response":
        return {"sync_response": response.sync_response}
    if kind != "update":
        raise ValueError(f"Unexpected gNMI response: {kind}")
    note = response.update
    if note.timestamp <= 0:
        raise ValueError("gNMI notification has no valid device timestamp")
    return {
        "update": {
            "timestamp": note.timestamp,
            "prefix": gnmi_path_degenerator(note.prefix),
            "update": [
                {"path": gnmi_path_degenerator(u.path), "val": typed_value(u.val)} for u in note.update
            ],
            "delete": [gnmi_path_degenerator(p) for p in note.delete],
        }
    }
