"""Command-line entrypoints and dispatch for the Ragonometrics pipeline."""

def main() -> int:
    """Lazy CLI dispatcher to avoid import side effects.

    Returns:
        int: Computed integer result.
    """
    from .entrypoints import main as _main

    return _main()

__all__ = ["main"]
