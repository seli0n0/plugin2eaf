from __future__ import annotations

import ast
import os
import py_compile
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class ConversionError(Exception):
    """The input cannot safely be represented as an Elyx project."""


@dataclass(frozen=True)
class SourceFile:
    path: PurePosixPath
    data: bytes


ID_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
TOOL_NAME = "Plugin2EAF"
TOOL_URL = "https://github.com/seli0n0/plugin2eaf"
META_FIELDS = {
    "__id__": "id", "__name__": "name", "__description__": "description",
    "__author__": "author", "__version__": "version", "__icon__": "icon",
    "__app_version__": "app_version", "__sdk_version__": "sdk_version",
    "__min_version__": "min_version",
}


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] in ("", "."):
        raise ConversionError(f"Unsafe path in input archive: {name!r}")
    return path


def _read_input(input_path: Path) -> list[SourceFile]:
    if input_path.is_dir():
        files = []
        for item in input_path.rglob("*"):
            if item.is_file():
                files.append(SourceFile(PurePosixPath(item.relative_to(input_path).as_posix()), item.read_bytes()))
        return files
    if not input_path.is_file():
        raise ConversionError(f"Input does not exist: {input_path}")
    if zipfile.is_zipfile(input_path):
        files = []
        with zipfile.ZipFile(input_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                files.append(SourceFile(_safe_path(info.filename), archive.read(info)))
        return _strip_wrapper(files)
    return [SourceFile(PurePosixPath(input_path.name), input_path.read_bytes())]


def _strip_wrapper(files: list[SourceFile]) -> list[SourceFile]:
    if not files:
        raise ConversionError("Input archive is empty")
    first_parts = {file.path.parts[0] for file in files if len(file.path.parts) > 1}
    has_root_file = any(len(file.path.parts) == 1 for file in files)
    if len(first_parts) == 1 and not has_root_file:
        return [SourceFile(PurePosixPath(*f.path.parts[1:]), f.data) for f in files]
    return files


def _python_files(files: list[SourceFile]) -> list[SourceFile]:
    """Legacy single-file plugins may use the .plugin extension for Python."""
    return [file for file in files if file.path.suffix.lower() in {".py", ".plugin"}]


def _literal_metadata(source: bytes, name: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source.decode("utf-8-sig"), filename=name)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ConversionError(f"Invalid Python source {name}: {exc}") from exc
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for target in targets:
                if target in META_FIELDS or target == "__requirements__":
                    try:
                        values[target] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return values


def _select_entry(files: list[SourceFile]) -> SourceFile:
    candidates = _python_files(files)
    if not candidates:
        raise ConversionError("No Python source was found. A compiled-only or opaque .plugin cannot be migrated safely.")
    for file in candidates:
        if b"BasePlugin" in file.data:
            return file
    raise ConversionError("No BasePlugin reference was found in the input source; this does not look like an exteraGram plugin.")


def _metadata(files: list[SourceFile], entry: SourceFile) -> dict[str, Any]:
    raw = _literal_metadata(entry.data, entry.path.as_posix())
    metadata = {target: raw[source] for source, target in META_FIELDS.items() if source in raw}
    plugin_id = metadata.get("id")
    if not isinstance(plugin_id, str) or not ID_RE.fullmatch(plugin_id):
        stem = re.sub(r"[^A-Za-z0-9_]", "_", entry.path.stem)
        plugin_id = (stem if len(stem) >= 2 else "plugin")[:32]
        metadata["id"] = plugin_id
    metadata.setdefault("name", plugin_id)
    metadata.setdefault("version", "1.0.0")
    description = metadata.get("description", "")
    if not isinstance(description, str):
        description = str(description)
    if TOOL_NAME not in description:
        metadata["description"] = (description.rstrip() + "\n\n" if description else "") + f"Converted with [{TOOL_NAME}]({TOOL_URL})"
    requirements = raw.get("__requirements__")
    if isinstance(requirements, (list, tuple)) and all(isinstance(x, str) for x in requirements):
        metadata["requirements"] = ", ".join(requirements)
    elif isinstance(requirements, str):
        metadata["requirements"] = requirements
    return metadata


def _yaml_value(value: Any) -> str:
    if value is None:
        return '""'
    if not isinstance(value, str):
        value = str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _yaml(metadata: dict[str, Any]) -> bytes:
    order = ("id", "name", "description", "author", "version", "icon", "min_version", "app_version", "sdk_version", "requirements")
    return ("\n".join(f"{key}: {_yaml_value(metadata[key])}" for key in order if key in metadata) + "\n").encode("utf-8")


def _classified_paths(files: list[SourceFile], entry: SourceFile) -> dict[PurePosixPath, bytes]:
    result: dict[PurePosixPath, bytes] = {}
    for file in files:
        parts = file.path.parts
        root = parts[0].lower() if parts else ""
        if file.path.suffix.lower() in {".py", ".plugin"}:
            target = PurePosixPath("plugin/src/main.py") if file == entry else PurePosixPath("plugin/src", *parts)
        elif root in {"assets", "res"}:
            target = PurePosixPath("plugin/res", *parts[1:])
        elif root in {"strings", "locales", "locale"}:
            target = PurePosixPath("plugin/locales", *parts[1:])
        elif root == "wheels":
            target = PurePosixPath("wheels", *parts[1:])
        else:
            target = PurePosixPath("plugin/res/legacy", *parts)
        if target in result:
            raise ConversionError(f"Two files map to the same output path: {target}")
        result[target] = file.data
    return result


def _refmap(content: dict[PurePosixPath, bytes], main: str) -> bytes:
    """Declare optional Elyx directories only when the archive contains them."""
    lines = ["metainfo: plugin/meta.yml", f"main: {main}", 'plugin2eaf: "1.0.1"']
    paths = tuple(content)
    if any(path.parts[:2] == ("plugin", "res") for path in paths):
        lines.append("assets: plugin/res")
    if any(path.parts[:2] == ("plugin", "locales") for path in paths):
        lines.append("strings: plugin/locales")
    if any(path.parts[:1] == ("wheels",) for path in paths):
        lines.append("wheels: wheels")
    return ("\n".join(lines) + "\n").encode("utf-8")


def inspect_input(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path)
    files = _read_input(path)
    entry = _select_entry(files)
    metadata = _metadata(files, entry)
    return {
        "input": str(path), "files": len(files), "python_files": len(_python_files(files)),
        "entry": entry.path.as_posix(), "metadata": metadata,
        "archive_input": path.is_file() and zipfile.is_zipfile(path),
    }


def _write_zip(output: Path, content: dict[PurePosixPath, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        directories: set[PurePosixPath] = set()
        for path in content:
            parent = path.parent
            while str(parent) != ".":
                directories.add(parent)
                parent = parent.parent
        for directory in sorted(directories, key=lambda x: x.as_posix()):
            archive.writestr(directory.as_posix().rstrip("/") + "/", b"")
        for path, data in sorted(content.items(), key=lambda item: item[0].as_posix()):
            archive.writestr(path.as_posix(), data)


def _compile(content: dict[PurePosixPath, bytes]) -> dict[PurePosixPath, bytes]:
    if (os.sys.version_info.major, os.sys.version_info.minor) != (3, 11):
        raise ConversionError("--compile requires Python 3.11 so .pyc files match the exteraGram runtime.")
    result = dict(content)
    with tempfile.TemporaryDirectory(prefix="plugin2eaf-") as directory:
        root = Path(directory)
        for path, data in content.items():
            local = root / Path(*path.parts)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        for path in list(content):
            if path.suffix != ".py":
                continue
            source = root / Path(*path.parts)
            compiled = source.with_suffix(".pyc")
            try:
                py_compile.compile(str(source), cfile=str(compiled), doraise=True, optimize=2)
            except py_compile.PyCompileError as exc:
                raise ConversionError(str(exc)) from exc
            result[path.with_suffix(".pyc")] = compiled.read_bytes()
            del result[path]
    return result


def convert(input_path: str | Path, output_path: str | Path, *, compile_python: bool = False, force: bool = False) -> dict[str, Any]:
    output = Path(output_path)
    if output.suffix.lower() != ".eaf":
        raise ConversionError("Output must have the .eaf extension")
    if output.exists() and not force:
        raise ConversionError(f"Output exists: {output} (use --force to replace it)")
    files = _read_input(Path(input_path))
    entry = _select_entry(files)
    metadata = _metadata(files, entry)
    content = _classified_paths(files, entry)
    content[PurePosixPath("plugin/meta.yml")] = _yaml(metadata)
    content[PurePosixPath("refmap.yml")] = _refmap(content, "plugin/src/main.py")
    if compile_python:
        content = _compile(content)
        content[PurePosixPath("refmap.yml")] = _refmap(content, "plugin/src/main.pyc")
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        _write_zip(temporary, content)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    report = validate_archive(output)
    if not report["valid"]:
        raise ConversionError("Internal validation failed: " + "; ".join(report["errors"]))
    return {"output": str(output), "metadata": metadata, "files": len(content), "compiled": compile_python}


def validate_archive(archive_path: str | Path) -> dict[str, Any]:
    path = Path(archive_path)
    errors: list[str] = []
    if not path.is_file() or not zipfile.is_zipfile(path):
        return {"archive": str(path), "valid": False, "errors": ["Not a ZIP-compatible archive"]}
    with zipfile.ZipFile(path) as archive:
        names = {info.filename.rstrip("/") for info in archive.infolist()}
        for name in names:
            try:
                _safe_path(name)
            except ConversionError as exc:
                errors.append(str(exc))
        if "refmap.yml" not in names and "refmap.yaml" not in names and "refmap.json" not in names:
            errors.append("Missing root refmap file")
        if "plugin/meta.yml" not in names:
            errors.append("Missing plugin/meta.yml")
        if "plugin/src/main.py" not in names and "plugin/src/main.pyc" not in names:
            errors.append("Missing plugin entry module")
        for name in names:
            if name.endswith(".py"):
                try:
                    ast.parse(archive.read(name).decode("utf-8-sig"), filename=name)
                except (UnicodeDecodeError, SyntaxError) as exc:
                    errors.append(f"Invalid Python source {name}: {exc}")
    return {"archive": str(path), "valid": not errors, "errors": errors}
