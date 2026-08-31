"""cablegram: raw dispatches from tech, AI and Chinese/Russian sources."""

from importlib.metadata import PackageNotFoundError, version as _installed

try:
    __version__ = _installed("cablegram-mcp")
except PackageNotFoundError:
    # A checkout that was never installed — PYTHONPATH=src rather than either
    # route the README offers, both of which register the metadata (`uv sync`
    # installs editable). Reported rather than guessed: a number written here
    # is wrong the moment it drifts, and this string is how a client says which
    # build it is talking to.
    __version__ = "0+unknown"

__all__ = ["__version__"]
