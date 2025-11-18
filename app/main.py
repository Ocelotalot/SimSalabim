"""Application entrypoint for the Bybit intraday bot.

The :func:`main` function currently orchestrates a lightweight bootstrap flow
that will later grow into the full control loop described in ARCHITECTURE.md.
Other subsystems (config, runtime, strategies, risk, execution, telemetry) will
be composed here once their implementations are available.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def main() -> None:
    """Initialize high-level services and start the runtime loop (placeholder).

    During this stage we only demonstrate the structure: load configs, read
    runtime state, initialize subsystems, then launch the orchestration loop.
    Actual implementations will be filled in future stages.
    """

    project_root = Path(__file__).resolve().parents[1]
    config_dir = project_root / "config"
    runtime_dir = project_root / "runtime"

    # In the future these helpers will deserialize YAML/JSON into structured
    # models. For now we simply log the paths for visibility during development.
    bootstrap_context: dict[str, Any] = {
        "config_dir": str(config_dir),
        "runtime_dir": str(runtime_dir),
    }

    print("[bootstrap] Starting Bybit intraday bot with context:")
    for key, value in bootstrap_context.items():
        print(f"  - {key}: {value}")

    # Placeholder for the main async/sync loop.
    print("[bootstrap] Initialization complete. Trading loop not yet implemented.")


if __name__ == "__main__":
    main()
