"""API connection profiles (M07B), read from a TOML file that holds no secrets.

A profile names the protocol, endpoint, model and the *name* of an environment variable
holding the key. The key value is read only when a request is sent and never stored,
logged or returned. A run seals its profile (without credentials) into its manifest.
"""

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

from genesis.cognition.prompts import DECISION_PROMPT_VERSION
from genesis.providers.base import ProviderError

PROTOCOLS = ("openai_chat", "replicate")
STRUCTURED = ("none", "json_object", "json_schema")
DEFAULT_FILE = Path(__file__).resolve().parents[3] / "config" / "providers.toml"
# Fields the adapter itself controls; a profile cannot override the prompt or the limits.
RESERVED_FIELDS = {
    "model",
    "messages",
    "prompt",
    "system_prompt",
    "max_tokens",
    "max_new_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "seed",
    "stream",
    "response_format",
    "version",
    "input",
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    protocol: str
    base_url: str
    model: str
    api_key_env: str = ""
    label: str = ""
    remote: bool = False
    timeout_s: float = 120.0
    max_output_tokens: int = 256
    reflection_max_output_tokens: int = 512  # reflection replies (M11) cite several ids
    max_context_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 0.95
    send_seed: bool = True
    structured_output: str = "none"
    max_requests: int = 2000  # per run; a technical budget, never an ecological rule
    max_total_tokens: int = 4_000_000
    # Endpoint-specific request fields, sent as given and recorded with every decision,
    # e.g. {chat_template_kwargs = {enable_thinking = false}} for servers of thinking models.
    extra_body: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _ID.match(self.id):
            raise ValueError(f"profile id {self.id!r} must be letters, digits, '-', '_' or '.'")
        if self.protocol not in PROTOCOLS:
            raise ValueError(f"profile {self.id}: protocol must be one of {PROTOCOLS}")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https"):
            raise ValueError(f"profile {self.id}: base_url must be http(s)")
        if url.scheme == "http" and url.hostname not in LOOPBACK:
            raise ValueError(
                f"profile {self.id}: plain http is allowed only for this machine; use https"
            )
        if self.structured_output not in STRUCTURED:
            raise ValueError(f"profile {self.id}: structured_output must be one of {STRUCTURED}")
        if not isinstance(self.extra_body, dict):
            raise ValueError(f"profile {self.id}: extra_body must be a table")
        reserved = set(self.extra_body) & RESERVED_FIELDS
        if reserved:
            raise ValueError(f"profile {self.id}: extra_body may not set {sorted(reserved)}")
        try:
            json.dumps(self.extra_body)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"profile {self.id}: extra_body must be plain data") from exc
        if self.protocol == "replicate" and self.structured_output != "none":
            raise ValueError(f"profile {self.id}: Replicate models take prompted JSON only")
        for name, low, high in (
            ("timeout_s", 1, 3600),
            ("max_output_tokens", 1, 32_768),
            ("reflection_max_output_tokens", 1, 32_768),
            ("max_context_tokens", 256, 1_048_576),
            ("temperature", 0, 2),
            ("top_p", 0, 1),
            ("max_requests", 1, 10_000_000),
            ("max_total_tokens", 1, 10_000_000_000),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"profile {self.id}: {name} must be a number")
            if not low <= value <= high:
                raise ValueError(f"profile {self.id}: {name} must be {low}–{high}")

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc

    @property
    def display(self) -> str:
        if self.label:
            return self.label
        return "Replicate" if self.protocol == "replicate" else self.host

    def key(self) -> str | None:
        """The credential value, read at request time only. None when none is configured."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None

    def key_status(self) -> str:
        if not self.api_key_env:
            return "not needed"
        return "set" if os.environ.get(self.api_key_env) else "missing"

    def to_dict(self) -> dict:
        return asdict(self)


def providers_file() -> Path:
    return Path(os.environ.get("GENESIS_PROVIDERS_FILE", str(DEFAULT_FILE)))


def load_profiles() -> tuple[dict[str, Profile], list[str]]:
    """All valid profiles, plus plain-language problems with invalid ones."""
    path = providers_file()
    if not path.is_file():
        return {}, []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, [f"{path.name}: {exc}"]
    known = {f.name for f in fields(Profile)} - {"id"}
    profiles: dict[str, Profile] = {}
    problems: list[str] = []
    for pid, raw in (data.get("profiles") or {}).items():
        if not isinstance(raw, dict):
            problems.append(f"profile {pid}: must be a table")
            continue
        unknown = set(raw) - known
        if unknown:
            problems.append(f"profile {pid}: unknown settings {sorted(unknown)}")
            continue
        try:
            profiles[pid] = Profile(id=pid, **raw)
        except (TypeError, ValueError) as exc:
            problems.append(str(exc))
    return profiles, problems


def get_profile(profile_id: str) -> Profile:
    profiles, _ = load_profiles()
    profile = profiles.get(profile_id)
    if profile is None:
        raise ProviderError("config", f"API profile {profile_id!r} is not configured")
    return profile


def list_profiles() -> dict:
    """What the observer may see: never a key, only whether one is present."""
    profiles, problems = load_profiles()
    return {
        "file": str(providers_file()),
        "problems": problems,
        "profiles": [
            {
                "id": p.id,
                "protocol": p.protocol,
                "label": p.display,
                "host": p.host,
                "model": p.model,
                "remote": p.remote,
                "key_status": p.key_status(),
                "api_key_env": p.api_key_env or None,
                "available": bool(p.model) and p.key_status() != "missing",
                "reason": None
                if p.model and p.key_status() != "missing"
                else (
                    "no model set in the profile" if not p.model else f"{p.api_key_env} is not set"
                ),
            }
            for p in profiles.values()
        ],
    }


def _unavailable(profile: Profile) -> str | None:
    if not profile.model:
        return "no model is set in the profile"
    if profile.key_status() == "missing":
        return f"{profile.api_key_env} is not set"
    return None


def sealed(profile: Profile, extra: dict | None = None) -> dict:
    """The manifest record of a run's API provider. It contains no credential."""
    return {
        "kind": "api",
        **profile.to_dict(),
        "prompt_version": DECISION_PROMPT_VERSION,
        **(extra or {}),
    }


def from_sealed(record: dict) -> Profile:
    known = {f.name for f in fields(Profile)}
    return Profile(**{k: v for k, v in record.items() if k in known})
