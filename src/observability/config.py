import os
from pathlib import Path
import yaml
from .schema import Device


def inventory():
    return [
        Device.model_validate(d)
        for d in yaml.safe_load(Path(os.getenv("INVENTORY", "lab/inventory.yml")).read_text())["devices"]
    ]


def profile():
    return yaml.safe_load(Path(os.getenv("EOS_PROFILE", "lab/eos-profile.yml")).read_text())


def credentials(device):
    return os.environ[device.username_env], os.environ[device.password_env]
