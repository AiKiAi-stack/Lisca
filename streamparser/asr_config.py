# encoding:utf-8
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict


class ASRProvider(Enum):
    DASHSCOPE = auto()


@dataclass
class ASRSettings:
    api_key: str
    provider: ASRProvider = ASRProvider.DASHSCOPE
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ASRSettings":
        data = deepcopy(data)
        if data.get("provider"):
            data["provider"] = ASRProvider[data.pop("provider").upper()]
        return cls(**data)


@dataclass
class SubtitleConfig:
    font_path: str = ""
    font_size: int = 32
    max_lines: int = 2
    position: str = "bottom"
    offset_y: int = 20
    text_color: str = "white"
    stroke_color: str = "black"
    stroke_width: int = 1


@dataclass
class ReconnectConfig:
    max_retries: int = 10
    base_delay: float = 1.0
    max_delay: float = 30.0
