"""Packaged release metadata; build manifests are checked against the same source."""

import json
from importlib.resources import files

_metadata = json.loads(files("app").joinpath("release.json").read_text(encoding="utf-8"))
VERSION: str = _metadata["version"]
API_VERSION: int = _metadata["api_version"]
