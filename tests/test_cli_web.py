"""CLI tests for Flask web launch options."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.cli import entrypoints


def test_web_parser_accepts_timeout_flags() -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "web",
            "--gunicorn",
            "--workers",
            "3",
            "--timeout",
            "180",
            "--graceful-timeout",
            "30",
            "--keep-alive",
            "5",
        ]
    )
    assert args.workers == 3
    assert args.timeout == 180
    assert args.graceful_timeout == 30
    assert args.keep_alive == 5


def test_cmd_web_passes_timeout_flags_to_gunicorn(monkeypatch) -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "web",
            "--gunicorn",
            "--host",
            "0.0.0.0",
            "--port",
            "8590",
            "--workers",
            "2",
            "--timeout",
            "180",
            "--graceful-timeout",
            "30",
            "--keep-alive",
            "5",
        ]
    )
    captured = {}

    def _fake_call(cmd):
        captured["cmd"] = list(cmd)
        return 0

    monkeypatch.setattr(entrypoints.subprocess, "call", _fake_call)
    rc = entrypoints.cmd_web(args)
    assert rc == 0
    cmd = captured["cmd"]
    assert "--timeout" in cmd and "180" in cmd
    assert "--graceful-timeout" in cmd and "30" in cmd
    assert "--keep-alive" in cmd and "5" in cmd


def test_web_cache_benchmark_parser_accepts_flags() -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "benchmark-web-cache",
            "--base-url",
            "http://localhost:8590",
            "--users",
            "25",
            "--iterations",
            "4",
            "--auth-mode",
            "shared-session",
            "--min-cache-ratio",
            "0.95",
        ]
    )
    assert args.base_url == "http://localhost:8590"
    assert args.users == 25
    assert args.iterations == 4
    assert args.auth_mode == "shared-session"
    assert float(args.min_cache_ratio) == 0.95


def test_cmd_benchmark_web_cache_writes_report(monkeypatch) -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "benchmark-web-cache",
            "--base-url",
            "http://localhost:8590",
            "--identifier",
            "admin",
            "--password",
            "pass123",
            "--users",
            "2",
            "--iterations",
            "2",
            "--out",
            "bench/web-cache-benchmark.json",
        ]
    )

    def _fake_benchmark(**kwargs):
        return {
            "config": {"auth_mode": "shared-session", "users": kwargs.get("users"), "base_url": kwargs.get("base_url")},
            "summary": {
                "target_iterations": 4,
                "successful_iterations": 4,
                "failed_iterations": 0,
                "iterations_per_second": 12.0,
            },
            "cache_coverage": {
                "avg_ratio": 1.0,
                "min_ratio": 1.0,
                "max_ratio": 1.0,
                "avg_cached_questions": 83.0,
                "avg_total_questions": 83.0,
            },
        }

    monkeypatch.setattr(entrypoints, "benchmark_web_cached_structured_questions", _fake_benchmark)
    monkeypatch.setattr(entrypoints, "write_web_cache_benchmark_report", lambda report, out_path: out_path)
    rc = entrypoints.cmd_benchmark_web_cache(args)
    assert rc == 0


def test_web_tabs_benchmark_parser_accepts_flags() -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "benchmark-web-tabs",
            "--base-url",
            "http://localhost:8590",
            "--users",
            "12",
            "--iterations",
            "3",
            "--no-openalex",
            "--network-max-references",
            "15",
        ]
    )
    assert args.users == 12
    assert args.iterations == 3
    assert args.no_openalex is True
    assert args.network_max_references == 15


def test_cmd_benchmark_web_tabs_writes_report(monkeypatch) -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "benchmark-web-tabs",
            "--base-url",
            "http://localhost:8590",
            "--identifier",
            "admin",
            "--password",
            "pass123",
            "--users",
            "2",
            "--iterations",
            "2",
            "--out",
            "bench/web-tabs-benchmark.json",
        ]
    )

    def _fake_benchmark(**kwargs):
        return {
            "config": {"auth_mode": "shared-session", "users": kwargs.get("users"), "base_url": kwargs.get("base_url")},
            "summary": {
                "target_iterations": 4,
                "successful_iterations": 4,
                "failed_iterations": 0,
                "iterations_per_second": 9.5,
            },
            "endpoints": {},
        }

    monkeypatch.setattr(entrypoints, "benchmark_web_tabs", _fake_benchmark)
    monkeypatch.setattr(entrypoints, "write_web_cache_benchmark_report", lambda report, out_path: out_path)
    rc = entrypoints.cmd_benchmark_web_tabs(args)
    assert rc == 0


def test_cmd_benchmark_web_chat_writes_report(monkeypatch) -> None:
    parser = entrypoints.build_parser()
    args = parser.parse_args(
        [
            "benchmark-web-chat",
            "--base-url",
            "http://localhost:8590",
            "--identifier",
            "admin",
            "--password",
            "pass123",
            "--users",
            "2",
            "--iterations",
            "2",
            "--min-cache-hit-ratio",
            "0.5",
            "--out",
            "bench/web-chat-benchmark.json",
        ]
    )

    def _fake_benchmark(**kwargs):
        return {
            "config": {"auth_mode": "shared-session", "users": kwargs.get("users"), "base_url": kwargs.get("base_url")},
            "summary": {
                "target_iterations": 4,
                "successful_iterations": 4,
                "failed_iterations": 0,
                "iterations_per_second": 7.0,
            },
            "chat_cache": {"cache_hit_ratio": 0.75, "hit_count": 3, "sample_count": 4, "layer_counts": {"strict": 2, "fallback": 1}},
            "endpoints": {},
        }

    monkeypatch.setattr(entrypoints, "benchmark_web_chat_turns", _fake_benchmark)
    monkeypatch.setattr(entrypoints, "write_web_cache_benchmark_report", lambda report, out_path: out_path)
    rc = entrypoints.cmd_benchmark_web_chat(args)
    assert rc == 0
