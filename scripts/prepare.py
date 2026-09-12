import os
import secrets
from pathlib import Path
from topology import configs

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    envfile = ROOT / ".env"
    if not envfile.exists():
        envfile.write_text(f"LAB_USERNAME=observer\nLAB_PASSWORD={secrets.token_hex(16)}\n")
    envfile.chmod(0o600)
    for line in envfile.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)
    # Reject config syntax injection; generated passwords meet these constraints.
    import re

    for key in ("LAB_USERNAME", "LAB_PASSWORD"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", os.environ[key]):
            raise ValueError(f"{key} must contain only letters, numbers, underscores or hyphens")
    for name in ("opensearch", "prometheus", "grafana", "discovery", "evidence"):
        path = ROOT / "runtime" / name
        path.mkdir(parents=True, exist_ok=True)
        if name in ("opensearch", "prometheus", "grafana", "discovery"):
            path.chmod(0o777)  # Different image UIDs; lab data only, isolated below runtime.
    discovery = ROOT / "runtime/discovery/gnmi.json"
    if not discovery.exists():
        discovery.write_text("[]\n")
    configs(ROOT, os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"])


if __name__ == "__main__":
    prepare()
