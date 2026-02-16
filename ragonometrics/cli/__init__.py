"""Command-line entrypoints and dispatch for the Ragonometrics pipeline."""

def main() -> int:
    """Lazy CLI dispatcher to avoid import side effects."""
    from .entrypoints import main as _main

    return _main()

__all__ = ["main"]
