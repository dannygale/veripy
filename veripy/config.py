"""veripy.toml project configuration: schema, loader, and search."""

import os
import sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_CONFIG_FILE = "veripy.toml"

# ── Schema defaults ────────────────────────────────────────────────────────────

_DEFAULTS = {
    "project": {"name": "", "version": "0.1.0", "description": ""},
    "build":   {"top": "", "output": "rtl/", "module": None, "params": {}},
    "test":    {"path": "tests/"},
    "fpga":    {"family": "", "device": "", "constraints": ""},
}

_VALID_FPGA_FAMILIES = {"xilinx", "intel", "lattice", "gowin", "efinix", ""}


def _validate(cfg: dict) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors = []
    build = cfg.get("build", {})
    if build.get("top") and not build["top"].endswith(".py"):
        errors.append("[build] top must be a .py file")
    fpga = cfg.get("fpga", {})
    if fpga.get("family") and fpga["family"] not in _VALID_FPGA_FAMILIES:
        errors.append(f"[fpga] unknown family '{fpga['family']}'")
    return errors


def load_config(path: str | None = None) -> dict | None:
    """Load and return the veripy.toml config, or None if not found.

    Searches *path* first, then walks up from cwd to find veripy.toml.
    Exits with an error message if the file exists but is invalid.
    """
    if tomllib is None:
        sys.exit(
            "error: TOML support requires Python 3.11+ or 'pip install tomli'"
        )

    cfg_path = path or _find_config()
    if cfg_path is None:
        return None

    try:
        with open(cfg_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as e:
        sys.exit(f"error: could not parse {cfg_path}: {e}")

    # Merge with defaults
    cfg: dict = {}
    for section, defaults in _DEFAULTS.items():
        cfg[section] = {**defaults, **raw.get(section, {})}
    # Preserve unknown top-level sections
    for key in raw:
        if key not in cfg:
            cfg[key] = raw[key]

    errors = _validate(cfg)
    if errors:
        for e in errors:
            print(f"error: {cfg_path}: {e}", file=sys.stderr)
        sys.exit(1)

    cfg["_path"] = cfg_path
    cfg["_dir"] = os.path.dirname(os.path.abspath(cfg_path))
    return cfg


def _find_config() -> str | None:
    """Walk up from cwd looking for veripy.toml."""
    d = os.path.abspath(os.getcwd())
    while True:
        candidate = os.path.join(d, _CONFIG_FILE)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def find_config() -> str | None:
    """Return path to nearest veripy.toml, or None."""
    return _find_config()


def get_target(cfg: dict, name: str) -> dict:
    """Return merged [build] config for the named target.

    The target's keys override the base [build] section; params are merged
    (target params take precedence over base params).  Exits if the target
    is not defined.
    """
    targets = cfg.get("build", {}).get("targets", {})
    if name not in targets:
        available = list(targets.keys())
        sys.exit(
            f"error: target '{name}' not found. "
            f"Available: {available if available else ['(none)']}"
        )
    base = {k: v for k, v in cfg["build"].items() if k != "targets"}
    override = targets[name]
    merged = {**base, **override}
    merged["params"] = {**base.get("params", {}), **override.get("params", {})}
    return merged
