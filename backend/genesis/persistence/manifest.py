"""Versioned run manifests with canonical hashing.

A manifest is the authority for an executed run's identity, configuration, rules and
initial state. It is stored as canonical JSON and sealed with its SHA-256; any later
change to it is detected on load.
"""

import hashlib
import json
from dataclasses import dataclass

SUPPORTED_MANIFESTS = (1, 2, 3)  # 1: M02 run sealed by migration; 2: M03 runs; 3: M04+ runs
SUPPORTED_STATE_SCHEMAS = tuple(range(2, 12))  # 2: M02; 3: +PRNG; 4: +organisms; 5: +material
# 6: +cognition scheduling; 7: +costly memory; 8: +local exchange; 9: +tendencies;
# 10: +reflection; 11: +reproduction


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Manifest:
    data: dict
    text: str
    digest: str

    @classmethod
    def seal(cls, data: dict) -> "Manifest":
        text = canonical_json(data)
        return cls(data, text, sha256(text))

    @classmethod
    def verify(cls, text: str | None, digest: str | None) -> "Manifest":
        if not text or not digest:
            raise ValueError("Run manifest is missing or unsealed")
        if sha256(text) != digest:
            raise ValueError("Run manifest checksum does not match its contents")
        data = json.loads(text)
        if text != canonical_json(data):
            raise ValueError("Run manifest is not canonical JSON")
        if data.get("manifest_version") not in SUPPORTED_MANIFESTS:
            raise ValueError(f"Unsupported manifest version {data.get('manifest_version')!r}")
        if data.get("state_schema") not in SUPPORTED_STATE_SCHEMAS:
            raise ValueError(f"Unsupported state schema {data.get('state_schema')!r}")
        return cls(data, text, digest)
