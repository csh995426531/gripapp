from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from grip.paths import ensure_home


DEFAULT_CONFIG = """# Grip cloud config — NO secrets here. Use official CLIs for credentials.
# ~/.grip/config.toml

[oracle]
# compartment_id = "ocid1.compartment.oc1..xxxx"
# profile = "DEFAULT"
# regions = ["ap-tokyo-1"]

[gcp]
# projects = ["my-gcp-project"]
# configuration = ""   # optional gcloud configuration name

[aliyun]
# [[aliyun.accounts]]
# profile = "default"
# account = "my-ali"
# regions = ["cn-hangzhou"]

[tencent]
# Use Lighthouse by default; set product = "cvm" for CVM.
# product = "lighthouse"
# regions = ["ap-guangzhou"]
"""


def config_path() -> Path:
    return ensure_home() / "config.toml"


def ensure_config() -> Path:
    path = config_path()
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


def load_config() -> dict[str, Any]:
    path = ensure_config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data or {}
