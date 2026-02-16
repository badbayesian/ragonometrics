from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


class CommandError(RuntimeError):
    def __init__(self, message: str, stderr: str = "", stdout: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr
        self.stdout = stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Markdown to LaTeX and PDF using pandoc and TeX.",
        epilog=(
            "Examples:\n"
            "  python tools/markdown_to_latex_pdf.py --input reports/audit.md\n"
            "  python tools/markdown_to_latex_pdf.py --input reports/audit.md "
            "--output-tex reports/audit.tex --output-pdf reports/audit.pdf\n"
            "  python tools/markdown_to_latex_pdf.py --input reports/audit.md --engine pdflatex"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Path to source Markdown file.")
    parser.add_argument(
        "--output-tex",
        default=None,
        help="Output LaTeX path (default: <input-stem>.tex in input directory).",
    )
    parser.add_argument(
        "--output-pdf",
        default=None,
        help="Output PDF path (default: <input-stem>.pdf in input directory).",
    )
    parser.add_argument(
        "--engine",
        choices=("xelatex", "pdflatex"),
        default="xelatex",
        help="TeX engine for compilation (default: xelatex).",
    )
    parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Keep intermediate LaTeX build files (.aux, .log, etc.).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error logs.",
    )
    return parser.parse_args()


def check_binary(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if os.name != "nt":
        return None
    for candidate in _windows_binary_candidates(name):
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _windows_binary_candidates(name: str) -> list[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates: dict[str, list[Path]] = {
        "pandoc": [
            Path(local_app_data) / "Pandoc" / "pandoc.exe",
            Path(local_app_data) / "Programs" / "Pandoc" / "pandoc.exe",
            Path(program_files) / "Pandoc" / "pandoc.exe",
        ],
    }
    return candidates.get(name, [])


def _tail(text: str, n: int = 2000) -> str:
    if not text:
        return ""
    return text[-n:]


def run_cmd(cmd: list[str], cwd: Path | None, quiet: bool) -> None:
    if not quiet:
        location = f" (cwd={cwd})" if cwd else ""
        print(f"[run]{location} {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        message = f"Command failed (exit {proc.returncode}): {' '.join(cmd)}"
        raise CommandError(message=message, stderr=proc.stderr, stdout=proc.stdout)
    if not quiet and proc.stdout.strip():
        output = proc.stdout.strip()
        try:
            print(output)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.buffer.write((output + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()


def convert_md_to_tex(input_md: Path, output_tex: Path, pandoc_bin: str, quiet: bool) -> None:
    cmd = [
        pandoc_bin,
        "--from",
        "gfm",
        "--to",
        "latex",
        "--standalone",
        "--wrap=preserve",
        "-o",
        str(output_tex),
        str(input_md),
    ]
    run_cmd(cmd, cwd=None, quiet=quiet)


def compile_tex_to_pdf(output_tex: Path, engine: str, engine_bin: str, quiet: bool) -> Path:
    tex_dir = output_tex.parent
    expected_pdf = output_tex.with_suffix(".pdf")
    latexmk_path = check_binary("latexmk")
    if latexmk_path:
        if engine == "xelatex":
            cmd = [latexmk_path, "-xelatex", "-interaction=nonstopmode", "-halt-on-error", output_tex.name]
        else:
            cmd = [latexmk_path, "-pdf", "-interaction=nonstopmode", "-halt-on-error", output_tex.name]
        run_cmd(cmd, cwd=tex_dir, quiet=quiet)
        return expected_pdf

    for _ in range(2):
        cmd = [
            engine_bin,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-output-directory",
            str(tex_dir),
            str(output_tex),
        ]
        run_cmd(cmd, cwd=None, quiet=quiet)
    return expected_pdf


def cleanup_aux_files(output_tex: Path, keep_intermediates: bool, quiet: bool) -> None:
    if keep_intermediates:
        return

    exts = (
        ".aux",
        ".bbl",
        ".bcf",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".lof",
        ".log",
        ".lot",
        ".nav",
        ".out",
        ".run.xml",
        ".snm",
        ".synctex.gz",
        ".toc",
    )
    for ext in exts:
        candidate = output_tex.with_suffix(ext)
        if candidate.exists():
            try:
                candidate.unlink()
                if not quiet:
                    print(f"[cleanup] Removed {candidate}")
            except OSError as exc:
                if not quiet:
                    print(f"[warn] Could not remove {candidate}: {exc}")


def _print_missing_binary_error(name: str, install_hint: str) -> None:
    print(f"[error] Required executable '{name}' was not found in PATH.", file=sys.stderr)
    print(f"[hint] {install_hint}", file=sys.stderr)


def _warn_non_md_extension(input_md: Path) -> None:
    if input_md.suffix.lower() != ".md":
        print(
            f"[warn] Input file extension is '{input_md.suffix}'. "
            "Proceeding anyway (expected .md).",
            file=sys.stderr,
        )


def _resolve_output_paths(input_md: Path, output_tex_arg: str | None, output_pdf_arg: str | None) -> tuple[Path, Path]:
    default_tex = input_md.with_suffix(".tex")
    default_pdf = input_md.with_suffix(".pdf")
    output_tex = Path(output_tex_arg).expanduser().resolve() if output_tex_arg else default_tex.resolve()
    output_pdf = Path(output_pdf_arg).expanduser().resolve() if output_pdf_arg else default_pdf.resolve()
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    return output_tex, output_pdf


def _finalize_pdf(compiled_pdf: Path, output_pdf: Path) -> None:
    if not compiled_pdf.exists():
        raise FileNotFoundError(f"Expected compiled PDF was not found: {compiled_pdf}")
    if compiled_pdf.resolve() == output_pdf.resolve():
        return
    try:
        shutil.copyfile(compiled_pdf, output_pdf)
    except UnicodeEncodeError:
        # Fallback for rare Windows codepage issues with metadata-aware copy helpers.
        output_pdf.write_bytes(compiled_pdf.read_bytes())


def _print_compile_failure_help(output_tex: Path, err: CommandError) -> None:
    print(f"[error] LaTeX compilation failed for {output_tex}", file=sys.stderr)
    if err.stderr.strip():
        print("[stderr tail]", file=sys.stderr)
        print(_tail(err.stderr), file=sys.stderr)
    elif err.stdout.strip():
        print("[stdout tail]", file=sys.stderr)
        print(_tail(err.stdout), file=sys.stderr)
    log_path = output_tex.with_suffix(".log")
    print(f"[hint] Inspect TeX log for details: {log_path}", file=sys.stderr)


def _print_pandoc_failure_help(err: CommandError) -> None:
    print("[error] Pandoc conversion failed.", file=sys.stderr)
    if err.stderr.strip():
        print("[stderr tail]", file=sys.stderr)
        print(_tail(err.stderr), file=sys.stderr)
    elif err.stdout.strip():
        print("[stdout tail]", file=sys.stderr)
        print(_tail(err.stdout), file=sys.stderr)


def main() -> int:
    args = parse_args()
    input_md = Path(args.input).expanduser().resolve()

    if not input_md.exists():
        print(f"[error] Input file does not exist: {input_md}", file=sys.stderr)
        return 1
    if not input_md.is_file():
        print(f"[error] Input path is not a file: {input_md}", file=sys.stderr)
        return 1

    _warn_non_md_extension(input_md)
    output_tex, output_pdf = _resolve_output_paths(input_md, args.output_tex, args.output_pdf)

    pandoc_bin = check_binary("pandoc")
    if not pandoc_bin:
        _print_missing_binary_error(
            "pandoc",
            "Install pandoc and ensure it is available in PATH (https://pandoc.org/installing.html).",
        )
        return 1
    engine_bin = check_binary(args.engine)
    if not engine_bin:
        _print_missing_binary_error(
            args.engine,
            f"Install a TeX distribution that provides '{args.engine}' (e.g., TeX Live/MiKTeX) and add it to PATH.",
        )
        return 1

    try:
        convert_md_to_tex(input_md=input_md, output_tex=output_tex, pandoc_bin=pandoc_bin, quiet=args.quiet)
    except CommandError as err:
        _print_pandoc_failure_help(err)
        return 1

    try:
        compiled_pdf = compile_tex_to_pdf(
            output_tex=output_tex,
            engine=args.engine,
            engine_bin=engine_bin,
            quiet=args.quiet,
        )
        _finalize_pdf(compiled_pdf=compiled_pdf, output_pdf=output_pdf)
    except CommandError as err:
        _print_compile_failure_help(output_tex, err)
        return 1
    except Exception as err:
        print(f"[error] Failed to produce final PDF: {err}", file=sys.stderr)
        return 1
    finally:
        cleanup_aux_files(output_tex=output_tex, keep_intermediates=args.keep_intermediates, quiet=args.quiet)

    print(f"[ok] LaTeX: {output_tex}")
    print(f"[ok] PDF: {output_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
