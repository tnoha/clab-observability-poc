"""Exercise a real SSH failure without changing router configuration."""

import logging
from datetime import datetime, timezone
from observability.cli import run
from observability.config import inventory
from observability.repository import Repository
from prepare import prepare
from lab import wait_for
from verify import latest, evidence


def check_cli_failure():
    prepare()
    devices = inventory()
    before = latest("ssh")
    # An unavailable port on the real edge1 container exercises connection failure.
    devices[2] = devices[2].model_copy(update={"ssh_port": 1})
    started = datetime.now(timezone.utc)
    assert run(devices, Repository("http://127.0.0.1:9200")) == 1

    def other_devices_updated():
        after = latest("ssh")
        for key, observation in before.items():
            updated = after[key]
            if observation["device"]["name"] == "edge1":
                assert updated["collected_at"] == observation["collected_at"]
            elif datetime.fromisoformat(updated["collected_at"].replace("Z", "+00:00")) < started:
                return False
        return True

    wait_for("CLI failure leaves edge1 unchanged and still collects five real devices", other_devices_updated)
    evidence("cli-failure", {"exit_code": 1, "other_devices_collected": 5, "failed_device": "edge1"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check_cli_failure()
