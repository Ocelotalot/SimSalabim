"""YAML loaders for the config subsystem.

Each helper here consumes one YAML file, validates it via models.py and
returns typed objects to the caller. The structure of the YAML files is
kept intentionally close to TZ.txt and ARCHITECTURE.md so future fields
can be added without rewriting loader logic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Sequence

import yaml

from .models import (
    ApiCredentialsConfig,
    AppConfig,
    StrategyRuntimeConfig,
    SymbolConfig,
    TradingConfig,
)

_DEFAULT_CONFIG_DIR = Path("config")


def _read_yaml(path: Path) -> Mapping:
    """Read a YAML file and return a mapping (empty dict if file is blank)."""

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"YAML root must be a mapping in {path}")
    return data


def load_trading_config(path: Path | str = _DEFAULT_CONFIG_DIR / "trading.yml") -> TradingConfig:
    """Load trading.yml (bybit.mode, risk, rotation, TF profiles, filters).

    The file mirrors TZ §2–4: root-level keys for `bybit.mode`, `update_interval_sec`,
    `risk`, `rotation`, `filters`, `tf_profiles`, `telemetry`. Adding new sections
    simply requires extending :class:`TradingConfig`.
    """

    data = _read_yaml(Path(path))
    return TradingConfig.parse_obj(data)


def load_symbols_config(path: Path | str = _DEFAULT_CONFIG_DIR / "symbols.yml") -> List[SymbolConfig]:
    """Load symbols.yml (list of instruments and limits per TZ §3.2)."""

    data = _read_yaml(Path(path))
    raw_symbols = data.get("symbols")
    if raw_symbols is None:
        raise ValueError("symbols.yml must contain `symbols: [...]`")
    if not isinstance(raw_symbols, Sequence):
        raise TypeError("`symbols` must be a list")
    return [SymbolConfig.parse_obj(entry) for entry in raw_symbols]


def load_strategies_config(path: Path | str = _DEFAULT_CONFIG_DIR / "strategies.yml") -> Dict[str, StrategyRuntimeConfig]:
    """Load strategies.yml (enabled strategies, priority and parameters).

    The file contains a list `strategies`, each with `id`, `enabled`, `priority`
    and arbitrary parameter map. Unknown keys survive inside `parameters` for
    forward compatibility when adding new strategy knobs.
    """

    data = _read_yaml(Path(path))
    raw_strategies = data.get("strategies", [])
    if not isinstance(raw_strategies, Sequence):
        raise TypeError("`strategies` must be a list")
    parsed: Dict[str, StrategyRuntimeConfig] = {}
    for entry in raw_strategies:
        cfg = StrategyRuntimeConfig.parse_obj(entry)
        parsed[cfg.id] = cfg
    return parsed


def load_secrets_config(path: Path | str = _DEFAULT_CONFIG_DIR / "secrets.yaml") -> ApiCredentialsConfig:
    """Load secrets.yaml (Bybit API keys and Telegram credentials).

    Uses the schema from credentials.example.yml. In production setups the file
    is gitignored; for tests it can point to a fixture.
    """

    data = _read_yaml(Path(path))
    return ApiCredentialsConfig.parse_obj(data)


def load_app_config(
    *,
    trading_path: Path | str = _DEFAULT_CONFIG_DIR / "trading.yml",
    symbols_path: Path | str = _DEFAULT_CONFIG_DIR / "symbols.yml",
    strategies_path: Path | str = _DEFAULT_CONFIG_DIR / "strategies.yml",
    secrets_path: Path | str = _DEFAULT_CONFIG_DIR / "secrets.yaml",
) -> AppConfig:
    """Load and aggregate all config sections into a single AppConfig.

    This is the entry point used by app.main: trading.yml + symbols.yml +
    strategies.yml + secrets.yaml get validated separately and then combined.
    Future config files (e.g. data feed settings) can be merged here by adding
    more parameters and fields to :class:`AppConfig`.
    """

    trading = load_trading_config(trading_path)
    symbols = load_symbols_config(symbols_path)
    strategies = load_strategies_config(strategies_path)
    credentials = load_secrets_config(secrets_path)
    return AppConfig(
        trading=trading,
        symbols=symbols,
        strategies=strategies,
        credentials=credentials,
    )
