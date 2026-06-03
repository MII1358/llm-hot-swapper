"""
Portable sticky failover extracted from a Hermes swapper setup.

This version is sanitized for sharing:
- no bundled secrets
- no personal headers
- configurable model cycle
- safe installer support for other Hermes instances
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "hot_swapper.config.json"
EXAMPLE_CONFIG_PATH = SCRIPT_DIR / "hot_swapper.config.example.json"


class SlotStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"
    COOLDOWN = "cooldown"


@dataclass
class APISlot:
    id: str
    label: str
    api_key: str
    model: str
    base_url: str
    priority: int = 0
    status: SlotStatus = SlotStatus.HEALTHY
    degraded_at: float | None = None
    cooldown_until: float | None = None
    fail_count: int = 0


@dataclass
class HotSwapperSettings:
    provider_pool: str
    model_cycle: list[str]
    slot_model_overrides: dict[str, str]
    default_model_fallback: str
    check_interval_seconds: int
    cooldown_seconds: int
    request_timeout_seconds: int
    healthcheck_prompt: str
    sticky_swap: bool
    persist_auth_priority: bool
    persist_config_default: bool
    status_map: dict[str, str]
    request_headers: dict[str, str]
    test_prompt: str


def resolve_hermes_home(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()

    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    default_home = Path.home() / ".hermes"
    if (default_home / "auth.json").exists():
        return default_home

    windows_alt = Path.home() / "AppData" / "Local" / ".openworld" / "hermes"
    if (windows_alt / "auth.json").exists():
        return windows_alt

    return default_home


def load_settings(config_path: Path) -> HotSwapperSettings:
    if not config_path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"Config not found: {config_path}. Copy {EXAMPLE_CONFIG_PATH.name} to "
                f"{DEFAULT_CONFIG_PATH.name} and edit it first."
            )
        raise FileNotFoundError(f"Config not found: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    return HotSwapperSettings(
        provider_pool=data.get("provider_pool", "openrouter"),
        model_cycle=data.get("model_cycle", []),
        slot_model_overrides=data.get("slot_model_overrides", {}),
        default_model_fallback=data.get("default_model_fallback", "openrouter/auto"),
        check_interval_seconds=int(data.get("check_interval_seconds", 300)),
        cooldown_seconds=int(data.get("cooldown_seconds", 60)),
        request_timeout_seconds=int(data.get("request_timeout_seconds", 60)),
        healthcheck_prompt=data.get("healthcheck_prompt", "ping"),
        sticky_swap=bool(data.get("sticky_swap", True)),
        persist_auth_priority=bool(data.get("persist_auth_priority", True)),
        persist_config_default=bool(data.get("persist_config_default", True)),
        status_map=data.get("status_map", {"dead": "dead", "degraded": "exhausted"}),
        request_headers=data.get("request_headers", {"X-Title": "Hot Swapper"}),
        test_prompt=data.get("test_prompt", "Reply with just: OK"),
    )


def build_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hot_swapper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "[%(asctime)s] [HOT_SWAPPER] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(log_dir / "hot_swapper.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def read_config_default_model(config_file: Path, fallback_model: str) -> str:
    if not config_file.exists():
        return fallback_model

    try:
        text = config_file.read_text(encoding="utf-8")
    except OSError:
        return fallback_model

    match = re.search(r"^\s*default:\s*(\S+)", text, re.MULTILINE)
    if not match:
        return fallback_model
    return match.group(1)


def write_config_default_model(config_file: Path, model: str) -> bool:
    if not config_file.exists():
        return False

    text = config_file.read_text(encoding="utf-8")
    updated = re.sub(
        r"^(\s*default:\s*)\S+",
        lambda match: f"{match.group(1)}{model}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == text:
        return False

    config_file.write_text(updated, encoding="utf-8")
    return True


def resolve_slot_model(
    slot_id: str,
    cycle_index: int,
    settings: HotSwapperSettings,
    config_default_model: str,
) -> str:
    override = settings.slot_model_overrides.get(slot_id)
    if override:
        return override

    if settings.model_cycle:
        return settings.model_cycle[cycle_index % len(settings.model_cycle)]

    return config_default_model or settings.default_model_fallback


def load_slots_from_auth(
    auth_file: Path,
    config_default_model: str,
    settings: HotSwapperSettings,
) -> list[APISlot]:
    if not auth_file.exists():
        raise FileNotFoundError(f"auth.json not found: {auth_file}")

    auth = json.loads(auth_file.read_text(encoding="utf-8"))
    pool = auth.get("credential_pool", {}).get(settings.provider_pool, [])
    sorted_pool = sorted(pool, key=lambda entry: entry.get("priority", 0))

    slots: list[APISlot] = []
    for index, entry in enumerate(sorted_pool):
        status_value = entry.get("last_status")
        slot = APISlot(
            id=entry.get("id", f"slot_{index + 1}"),
            label=entry.get("label", f"slot_{index + 1}"),
            api_key=entry.get("access_token", ""),
            model=resolve_slot_model(
                entry.get("id", f"slot_{index + 1}"),
                index,
                settings,
                config_default_model,
            ),
            base_url=entry.get("base_url", "https://openrouter.ai/api/v1"),
            priority=int(entry.get("priority", index)),
        )
        if status_value == settings.status_map.get("dead", "dead"):
            slot.status = SlotStatus.DEAD
        elif status_value == settings.status_map.get("degraded", "exhausted"):
            slot.status = SlotStatus.DEGRADED
        slots.append(slot)

    return slots


class APISwapper:
    SWAP_TRIGGERS = [
        "connection error",
        "api call failed after 3 retries",
        "api failed after 3 retries",
        "timed out",
        "timeout",
        "empty response",
    ]

    def __init__(
        self,
        slots: list[APISlot],
        settings: HotSwapperSettings,
        auth_file: Path,
        config_file: Path,
        logger: logging.Logger,
    ) -> None:
        self.slots = sorted(slots, key=lambda slot: slot.priority)
        self.settings = settings
        self.auth_file = auth_file
        self.config_file = config_file
        self.log = logger
        self._last_health_check = 0.0

    def call(self, messages: list[dict[str, Any]], max_tokens: int = 1000, **kwargs: Any) -> dict[str, Any]:
        self._maybe_health_check()
        tried: list[str] = []

        for slot in self._available_slots():
            try:
                result = self._request(slot, messages, max_tokens=max_tokens, **kwargs)
                if self.settings.sticky_swap and self.slots and self.slots[0].id != slot.id:
                    self.log.info(
                        "Sticky swap: %s (%s) becomes the new default slot",
                        slot.id,
                        slot.model,
                    )
                    self._rotate_to_default(slot)
                if tried:
                    self.log.info("Recovered via failover on %s after trying %s", slot.id, ", ".join(tried))
                return {"result": result, "slot_id": slot.id, "swapped": bool(tried)}
            except Exception as exc:
                tried.append(slot.id)
                self._handle_error(slot, str(exc).lower())

        raise RuntimeError("All API slots are degraded or unavailable.")

    def _request(
        self,
        slot: APISlot,
        messages: list[dict[str, Any]],
        max_tokens: int,
        **kwargs: Any,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {slot.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.settings.request_headers)

        response = requests.post(
            f"{slot.base_url}/chat/completions",
            headers=headers,
            json={"model": slot.model, "messages": messages, "max_tokens": max_tokens, **kwargs},
            timeout=self.settings.request_timeout_seconds,
        )

        if response.status_code == 401:
            raise RuntimeError(f"HTTP 401 dead key: {slot.id}")
        if response.status_code == 429:
            raise RuntimeError(f"HTTP 429 rate limit: {slot.id}")
        if response.status_code >= 500:
            raise RuntimeError(f"HTTP {response.status_code} server error: {slot.id}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {slot.id}") from exc

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"empty response from {slot.id}")
        return content

    def _handle_error(self, slot: APISlot, error_text: str) -> None:
        slot.fail_count += 1

        if "401" in error_text or "dead key" in error_text:
            slot.status = SlotStatus.DEAD
            self.log.error("DEAD %s (%s): invalid key", slot.id, slot.label)
            self._persist_slot_status()
            return

        if "429" in error_text or "rate limit" in error_text:
            slot.status = SlotStatus.COOLDOWN
            slot.cooldown_until = time.time() + self.settings.cooldown_seconds
            self.log.warning("COOLDOWN %s for %ss", slot.id, self.settings.cooldown_seconds)
            self._persist_slot_status()
            return

        if any(trigger in error_text for trigger in self.SWAP_TRIGGERS):
            slot.status = SlotStatus.DEGRADED
            slot.degraded_at = time.time()
            self.log.warning("SWAP %s due to: %s", slot.id, error_text[:120])
            self._persist_slot_status()
            return

        slot.status = SlotStatus.DEGRADED
        slot.degraded_at = time.time()
        self.log.warning("DEGRADED %s due to: %s", slot.id, error_text[:120])
        self._persist_slot_status()

    def _available_slots(self) -> list[APISlot]:
        now = time.time()
        result: list[APISlot] = []
        for slot in self.slots:
            if slot.status == SlotStatus.HEALTHY:
                result.append(slot)
                continue
            if slot.status == SlotStatus.COOLDOWN and slot.cooldown_until and now >= slot.cooldown_until:
                slot.status = SlotStatus.HEALTHY
                slot.cooldown_until = None
                self.log.info("Cooldown finished for %s", slot.id)
                result.append(slot)
        return result

    def _maybe_health_check(self) -> None:
        now = time.time()
        if now - self._last_health_check < self.settings.check_interval_seconds:
            return
        self._last_health_check = now

        for slot in self.slots:
            if slot.status != SlotStatus.DEGRADED:
                continue
            try:
                self._request(
                    slot,
                    [{"role": "user", "content": self.settings.healthcheck_prompt}],
                    max_tokens=5,
                )
                slot.status = SlotStatus.HEALTHY
                slot.fail_count = 0
                self.log.info("RECOVERY %s (%s) -> healthy", slot.id, slot.label)
            except Exception:
                self.log.debug("Health check still failing for %s", slot.id)
        self._persist_slot_status()

    def _rotate_to_default(self, winner: APISlot) -> None:
        self.slots = [slot for slot in self.slots if slot.id != winner.id]
        self.slots.insert(0, winner)
        for index, slot in enumerate(self.slots):
            slot.priority = index
        self._persist_slot_status()

        if self.settings.persist_config_default:
            if write_config_default_model(self.config_file, winner.model):
                self.log.info("Updated config.yaml default model to %s", winner.model)

    def _persist_slot_status(self) -> None:
        if not self.auth_file.exists():
            return

        auth = json.loads(self.auth_file.read_text(encoding="utf-8"))
        pool = auth.get("credential_pool", {}).get(self.settings.provider_pool, [])
        by_id = {slot.id: slot for slot in self.slots}

        for entry in pool:
            slot = by_id.get(entry.get("id"))
            if not slot:
                continue

            if self.settings.persist_auth_priority:
                entry["priority"] = slot.priority

            if slot.status == SlotStatus.DEAD:
                entry["last_status"] = self.settings.status_map.get("dead", "dead")
            elif slot.status == SlotStatus.DEGRADED:
                entry["last_status"] = self.settings.status_map.get("degraded", "exhausted")
            else:
                entry["last_status"] = "ok"

        self.auth_file.write_text(json.dumps(auth, indent=2, ensure_ascii=False), encoding="utf-8")

    def status_report(self) -> str:
        lines = ["=== Hot Swapper Status ==="]
        for slot in self.slots:
            marker = {
                SlotStatus.HEALTHY: "[OK]",
                SlotStatus.DEGRADED: "[DEGRADED]",
                SlotStatus.DEAD: "[DEAD]",
                SlotStatus.COOLDOWN: "[COOLDOWN]",
            }[slot.status]
            lines.append(
                f"{marker} {slot.label} [{slot.id}] -> {slot.model} | "
                f"priority={slot.priority} fails={slot.fail_count}"
            )
        return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hot Swapper")
    parser.add_argument("action", choices=["status", "check", "test"])
    parser.add_argument("--config", help="Path to hot_swapper.config.json")
    parser.add_argument("--hermes-home", help="Path to Hermes home directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG_PATH
    try:
        settings = load_settings(config_path)
    except FileNotFoundError as exc:
        print(str(exc))
        sys.exit(1)

    hermes_home = resolve_hermes_home(args.hermes_home)
    auth_file = hermes_home / "auth.json"
    config_file = hermes_home / "config.yaml"
    log_dir = (hermes_home / "swapper" / "logs") if hermes_home.exists() else (SCRIPT_DIR / "logs")
    logger = build_logger(log_dir)

    config_default_model = read_config_default_model(config_file, settings.default_model_fallback)
    try:
        slots = load_slots_from_auth(auth_file, config_default_model, settings)
    except FileNotFoundError as exc:
        print(str(exc))
        sys.exit(1)
    if not slots:
        print(f"No slots found in credential_pool.{settings.provider_pool}")
        sys.exit(1)

    swapper = APISwapper(slots, settings, auth_file, config_file, logger)

    if args.action == "status":
        print(swapper.status_report())
        return

    if args.action == "check":
        for slot in swapper.slots:
            try:
                swapper._request(
                    slot,
                    [{"role": "user", "content": settings.healthcheck_prompt}],
                    max_tokens=5,
                )
                print(f"[OK] {slot.label} [{slot.id}] -> {slot.model}")
            except Exception as exc:
                print(f"[FAIL] {slot.label} [{slot.id}] -> {slot.model} | {exc}")
        print(swapper.status_report())
        return

    if args.action == "test":
        response = swapper.call(
            [{"role": "user", "content": settings.test_prompt}],
            max_tokens=10,
        )
        print(f"Response from {response['slot_id']}: {response['result']}")
        if response["swapped"]:
            print("Failover was used.")
        print(swapper.status_report())


if __name__ == "__main__":
    main()
