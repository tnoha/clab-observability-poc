from datetime import datetime, timezone
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime


def now():
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Device(StrictModel):
    id: int
    name: str
    host: str
    platform: str = "eos"
    software_version: str = "4.34.0F"
    username_env: str = "LAB_USERNAME"
    password_env: str = "LAB_PASSWORD"
    ssh_port: int = 22
    gnmi_port: int = 6030


class DeviceIdentity(StrictModel):
    id: int
    name: str


class Source(StrictModel):
    transport: Literal["ssh", "gnmi"]
    collector: str
    platform: str = "eos"
    software_version: str = "4.34.0F"
    command: str | None = None
    path: str | None = None
    timestamp_origin: Literal["collector", "device"] = "collector"


class Interface(StrictModel):
    kind: Literal["interface"] = "interface"
    interface: str
    admin_state: Literal["up", "down", "unknown"] | None = None
    oper_state: Literal["up", "down", "unknown"] | None = None
    description: str | None = None
    mtu: int | None = None
    in_octets: int | None = Field(default=None, ge=0)
    out_octets: int | None = Field(default=None, ge=0)
    in_errors: int | None = Field(default=None, ge=0)
    out_errors: int | None = Field(default=None, ge=0)


class BgpNeighbor(StrictModel):
    kind: Literal["bgp_neighbor"] = "bgp_neighbor"
    vrf: str
    peer: str
    afi_safi: str = "ipv4-unicast"
    local_as: int | None = None
    remote_as: int | None = None
    session_state: str | None = None
    prefixes_received: int | None = None


class Observation(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    observation_type: Literal["interface", "bgp_neighbor"]
    observed_at: AwareDatetime
    collected_at: AwareDatetime = Field(default_factory=now)
    device: DeviceIdentity
    source: Source
    deleted: bool = False
    data: Annotated[Interface | BgpNeighbor, Field(discriminator="kind")]

    @property
    def entity_key(self):
        if isinstance(self.data, Interface):
            return f"{self.device.id}:interface:{self.data.interface}"
        return f"{self.device.id}:bgp:{self.data.vrf}:{self.data.peer}:{self.data.afi_safi}"


def observation(device, transport, data, timestamp=None, **source):
    return Observation(
        observation_type=data.kind,
        observed_at=timestamp or now(),
        device=DeviceIdentity(id=device.id, name=device.name),
        source=Source(
            transport=transport,
            collector=f"eos-{transport}",
            platform=device.platform,
            software_version=device.software_version,
            **source,
        ),
        data=data,
    )
