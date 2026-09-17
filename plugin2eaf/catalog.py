from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .core import ConversionError


PLUGIN_SUFFIXES = {".plugin", ".eaf", ".elyx"}
IGNORED_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", "build", "builds", "dist", "outputs"}


@dataclass(frozen=True)
class CatalogItem:
    name: str
    kind: str
    location: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _matches(item: CatalogItem, query: str | None) -> bool:
    needle = (query or "").casefold().strip()
    return not needle or needle in item.name.casefold() or needle in item.location.casefold()


def filter_items(items: Iterable[CatalogItem], query: str | None = None) -> list[CatalogItem]:
    return [item for item in items if _matches(item, query)]


def find_local(root: str | Path, query: str | None = None) -> list[CatalogItem]:
    directory = Path(root).expanduser().resolve()
    if not directory.is_dir():
        raise ConversionError(f"Plugin directory does not exist: {directory}")
    items: list[CatalogItem] = []
    for file in directory.rglob("*"):
        if any(part in IGNORED_DIRECTORIES for part in file.relative_to(directory).parts):
            continue
        if file.is_file() and file.suffix.lower() in PLUGIN_SUFFIXES:
            items.append(CatalogItem(file.name, file.suffix.lower()[1:], str(file), "local"))
    return sorted(filter_items(items, query), key=lambda item: item.name.casefold())


def fetch_github_catalog(repository: str, query: str | None = None, *, timeout: int = 20) -> list[CatalogItem]:
    if repository.count("/") != 1 or any(not part for part in repository.split("/")):
        raise ConversionError("Repository must use the owner/name format")
    api_url = f"https://api.github.com/repos/{repository}/git/trees/HEAD?recursive=1"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Plugin2EAF/1.2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ConversionError(f"GitHub repository is unavailable or private: {repository}") from exc
        raise ConversionError(f"GitHub returned HTTP {exc.code} while reading {repository}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConversionError(f"Cannot reach GitHub: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
    if payload.get("truncated"):
        raise ConversionError("GitHub tree is too large for a complete catalog; use a smaller repository")
    items = []
    for entry in payload.get("tree", []):
        path = entry.get("path", "")
        if entry.get("type") != "blob" or Path(path).suffix.lower() not in PLUGIN_SUFFIXES:
            continue
        items.append(CatalogItem(Path(path).name, Path(path).suffix.lower()[1:], f"https://raw.githubusercontent.com/{repository}/HEAD/{path}", "github"))
    return sorted(filter_items(items, query), key=lambda item: item.name.casefold())


def print_items(items: list[CatalogItem], write: Callable[[str], None] = print) -> None:
    if not items:
        write("No plugins found.")
        return
    width = max(len(item.name) for item in items)
    for number, item in enumerate(items, 1):
        write(f"{number:>3}. {item.name:<{width}}  [{item.kind}]  {item.source}")


def choose_item(items: list[CatalogItem], selection: int | None = None, *, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> CatalogItem:
    if not items:
        raise ConversionError("There are no plugins to select")
    print_items(items, write)
    if selection is None:
        answer = read("Choose a plugin number (0 to cancel): ").strip()
        if answer == "0":
            raise ConversionError("Selection cancelled")
        try:
            selection = int(answer)
        except ValueError as exc:
            raise ConversionError("Selection must be a number") from exc
    if not 1 <= selection <= len(items):
        raise ConversionError(f"Selection must be between 1 and {len(items)}")
    return items[selection - 1]


def download(item: CatalogItem, destination: str | Path) -> Path:
    target_dir = Path(destination).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / item.name
    if item.source == "local":
        shutil.copy2(item.location, target)
        return target
    request = urllib.request.Request(item.location, headers={"User-Agent": "Plugin2EAF/1.2"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConversionError(f"Cannot download {item.name}: {exc}") from exc
    return target
