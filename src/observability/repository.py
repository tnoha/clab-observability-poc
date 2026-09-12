import hashlib
import json
import os
import time
import requests

MAPPING = {
    "index_patterns": ["observations-*"],
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic_templates": [
                {"strings": {"match_mapping_type": "string", "mapping": {"type": "keyword"}}}
            ],
            "properties": {
                "observed_at": {"type": "date"},
                "collected_at": {"type": "date"},
                "deleted": {"type": "boolean"},
                "entity_key": {"type": "keyword"},
                "device": {"properties": {"id": {"type": "integer"}, "name": {"type": "keyword"}}},
            },
        },
    },
}


class Repository:
    def __init__(self, url=None):
        self.url = (url or os.getenv("OPENSEARCH_URL", "http://172.31.100.21:9200")).rstrip("/")

    def initialize(self):
        result = requests.put(f"{self.url}/_index_template/observations", json=MAPPING, timeout=10)
        result.raise_for_status()

    def write(self, observations):
        if not observations:
            return
        lines = []
        for obs in observations:
            doc = obs.model_dump(mode="json")
            doc["entity_key"] = obs.entity_key
            doc_id = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
            index = "observations-" + obs.collected_at.strftime("%Y.%m.%d")
            lines += [json.dumps({"index": {"_index": index, "_id": doc_id}}), json.dumps(doc)]
        payload = "\n".join(lines) + "\n"
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{self.url}/_bulk",
                    data=payload,
                    headers={"Content-Type": "application/x-ndjson"},
                    timeout=5,
                )
                response.raise_for_status()
                if response.json().get("errors"):
                    raise RuntimeError("OpenSearch bulk contains failed items")
                return
            except (requests.RequestException, RuntimeError):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
