"""Run webapp frontend tests with local npm or Docker fallback."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path) -> int:
    return subprocess.call(cmd, cwd=str(cwd))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run webapp frontend tests.")
    parser.add_argument(
        "--force-docker",
        action="store_true",
        help="Always run tests inside a Node Docker container.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    webapp_dir = repo_root / "webapp"
    if not webapp_dir.exists():
        print(f"webapp directory not found: {webapp_dir}")
        return 1

    npm_path = shutil.which("npm")
    if npm_path and not args.force_docker:
        print("Running webapp tests with local npm.")
        return _run(["npm", "run", "test"], cwd=webapp_dir)

    docker_path = shutil.which("docker")
    if not docker_path:
        print("npm is unavailable and Docker is not installed; cannot run frontend tests.")
        return 1

    compose_cmd = [
        "docker",
        "compose",
        "--profile",
        "ops",
        "run",
        "--rm",
        "--build",
        "frontend-tests",
    ]
    print("npm not found; running webapp tests via Docker Compose frontend-tests service.")
    compose_rc = _run(compose_cmd, cwd=repo_root)
    if compose_rc == 0:
        return 0

    print("Docker Compose run failed; attempting direct Docker build fallback (web-test target).")
    cmd = [
        "docker",
        "build",
        "--target",
        "web-test",
        "-f",
        "Dockerfile",
        ".",
    ]
    return _run(cmd, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
