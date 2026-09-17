from __future__ import annotations

import argparse
import json
import sys

from .core import ConversionError, convert, inspect_input, validate_archive

VERSION = "1.1.0"


def _print(value: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
    else:
        print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plugin2eaf", description="Convert legacy exteraGram plugins to Elyx .eaf archives.")
    parser.add_argument("--version", action="version", version=f"plugin2eaf {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Inspect an input plugin")
    inspect.add_argument("input")
    inspect.add_argument("--json", action="store_true")
    convert_parser = commands.add_parser("convert", help="Convert input to .eaf")
    convert_parser.add_argument("input")
    convert_parser.add_argument("--out", "-out", required=True, help="Output .eaf archive")
    convert_parser.add_argument("--compile", action="store_true", help="Compile with Python 3.11")
    convert_parser.add_argument("--force", action="store_true")
    validate = commands.add_parser("validate", help="Validate an .eaf archive")
    validate.add_argument("archive")
    validate.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            _print(inspect_input(args.input), args.json)
        elif args.command == "convert":
            _print(convert(args.input, args.out, compile_python=args.compile, force=args.force), False)
        else:
            report = validate_archive(args.archive)
            _print(report, args.json)
            return 0 if report["valid"] else 1
    except ConversionError as exc:
        print(f"plugin2eaf: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
