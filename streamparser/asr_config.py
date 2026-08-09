# encoding:utf-8
import os
import re
from dataclasses import dataclass


@dataclass
class ASRSettings:
    api_key: str
    workspace_id: str = ""
    region: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        match = re.match(r"^\$\{(\w+)\}$", self.api_key)
        if match:
            env_val = os.environ.get(match.group(1))
            if env_val is not None:
                self.api_key = env_val

    @property
    def websocket_url(self) -> str:
        return (
            f"wss://{self.workspace_id}.{self.region}.maas.aliyuncs.com"
            f"/api-ws/v1/inference"
        )
