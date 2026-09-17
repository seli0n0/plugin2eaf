from __future__ import annotations

import cmd
import shlex

from .catalog import choose_item, fetch_github_catalog, find_local
from .core import ConversionError, convert, inspect_input, validate_archive


class PluginShell(cmd.Cmd):
    intro = "Plugin2EAF interactive shell. Type help or ? to see commands."
    prompt = "plugin2eaf> "

    def _run(self, action):
        try:
            action()
        except (ConversionError, IndexError) as exc:
            self.stdout.write(f"Error: {exc}\n")

    def do_local(self, argument: str) -> None:
        "local DIRECTORY [QUERY]  Search local .plugin, .eaf and .elyx files."
        def action():
            parts = shlex.split(argument)
            if not parts:
                raise ConversionError("Usage: local DIRECTORY [QUERY]")
            item = choose_item(find_local(parts[0], " ".join(parts[1:])), write=lambda text: self.stdout.write(text + "\n"))
            self.stdout.write(f"Selected: {item.location}\n")
        self._run(action)

    def do_github(self, argument: str) -> None:
        "github OWNER/REPOSITORY [QUERY]  Search plugin archives in a public GitHub repository."
        def action():
            parts = shlex.split(argument)
            if not parts:
                raise ConversionError("Usage: github OWNER/REPOSITORY [QUERY]")
            item = choose_item(fetch_github_catalog(parts[0], " ".join(parts[1:])), write=lambda text: self.stdout.write(text + "\n"))
            self.stdout.write(f"Selected URL: {item.location}\n")
        self._run(action)

    def do_inspect(self, argument: str) -> None:
        "inspect FILE  Show detected plugin entry point and metadata."
        self._run(lambda: self.stdout.write(str(inspect_input(shlex.split(argument)[0])) + "\n"))

    def do_convert(self, argument: str) -> None:
        "convert INPUT OUTPUT.eaf  Convert one local legacy plugin."
        def action():
            parts = shlex.split(argument)
            if len(parts) != 2:
                raise ConversionError("Usage: convert INPUT OUTPUT.eaf")
            self.stdout.write(str(convert(parts[0], parts[1], force=True)) + "\n")
        self._run(action)

    def do_validate(self, argument: str) -> None:
        "validate FILE.eaf  Validate an EAF archive before installation."
        def action():
            report = validate_archive(shlex.split(argument)[0])
            self.stdout.write(str(report) + "\n")
        self._run(action)

    def do_exit(self, argument: str) -> bool:
        "exit  Leave the shell."
        return True

    do_quit = do_exit
    do_EOF = do_exit


def run_shell() -> int:
    try:
        PluginShell().cmdloop()
    except KeyboardInterrupt:
        print()
    return 0
