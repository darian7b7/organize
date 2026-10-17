#!/usr/bin/env python3
"""
Folder Organizer
================
Sorts the files in a folder into category subfolders (Images, Videos,
Documents, ...) and tidies loose subfolders into "Extracted Folders",
offering to merge ones that look like duplicates.

Safe by design:
  * Shows a preview first and asks before changing anything.
  * Never overwrites or deletes: name clashes get " (1)", " (2)", ...
    and leftovers from a merge go to "_trash" for you to review.
  * Skips hidden files/folders (like .git) and unfinished downloads.
  * Refuses to run on your home folder, a drive root or system folders.
  * Every run can be reversed with --undo.

Usage:
  python organize.py                 organize the folder this script is in
  python organize.py ~/Downloads     organize another folder
  python organize.py --dry-run       only show what would happen
  python organize.py --undo          reverse the last run

Requires Python 3.9+. No extra packages needed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from itertools import count
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings (edit these to customise the tool)
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, set[str]] = {
    "Images": {
        ".jpg", ".jpeg", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif",
        ".tiff", ".svg", ".ico", ".heic", ".heif", ".avif", ".psd", ".xcf",
        ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2",
    },
    "Videos": {
        ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v",
        ".mpg", ".mpeg", ".3gp", ".mts", ".m2ts", ".vob", ".ogv",
        ".srt", ".vtt", ".ass",  # subtitles live next to their videos
    },
    "Audio": {
        ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".oga", ".opus",
        ".wma", ".aiff", ".aif", ".alac", ".mid", ".midi", ".amr",
    },
    "Documents": {
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages",
        ".tex", ".xps", ".oxps",
    },
    "Spreadsheets": {".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".tsv", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "eBooks": {".epub", ".mobi", ".azw", ".azw3", ".fb2", ".djvu", ".cbz", ".cbr"},
    "Compressed": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst",
        ".lz", ".lzma", ".cab",
    },
    "Disk Images": {".iso", ".img", ".dmg", ".vhd", ".vhdx", ".vmdk", ".qcow2", ".ova"},
    "Executables": {
        ".exe", ".msi", ".msix", ".appx", ".bat", ".cmd", ".ps1", ".sh",
        ".run", ".appimage", ".deb", ".rpm", ".apk", ".pkg", ".jar",
        ".flatpakref",
    },
    "Code": {
        ".py", ".ipynb", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html",
        ".htm", ".css", ".scss", ".c", ".h", ".cpp", ".hpp", ".cs", ".java",
        ".kt", ".go", ".rs", ".rb", ".php", ".swift", ".lua", ".sql",
        ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    },
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "3D Models": {".stl", ".obj", ".fbx", ".blend", ".3mf", ".gltf", ".glb", ".step", ".stp"},
    "Torrents": {".torrent"},
}

OTHERS_DIR = "Others"
EXTRACTED_DIR = "Extracted Folders"
TRASH_DIR = "_trash"

LOG_NAME = "organizer_log.txt"
JOURNAL_NAME = ".organizer_journal.json"
MAX_UNDO_RUNS = 10

# Files that are still downloading. Moving them would break the download.
PARTIAL_DOWNLOAD_EXTS = {
    ".crdownload", ".part", ".partial", ".download", ".opdownload",
    ".tmp", ".crswap", ".!qb", ".!ut", ".aria2",
}
# System clutter that should stay where it is.
IGNORED_NAMES = {"desktop.ini", "thumbs.db", "ehthumbs.db", ".ds_store", "__pycache__"}

# Duplicate-folder detection: score = name part + content part (0..1).
NAME_WEIGHT = 0.4
CONTENT_WEIGHT = 0.6
DIFFERENT_NAME_SCORE = 0.4   # name score when the names differ
MERGE_THRESHOLD = 0.7        # at or above this, offer to merge

# Never organize these (unless --force). Paths for other OSes are ignored.
SYSTEM_DIRS = [
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/opt", "/proc",
    "/sbin", "/sys", "/usr", "/var", "/System", "/Library", "/Applications",
    "/private", "C:/Windows", "C:/Program Files", "C:/Program Files (x86)",
    "C:/ProgramData",
]

# ---------------------------------------------------------------------------
# Derived constants
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
EXTENSION_MAP = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
RESERVED_DIRS = set(CATEGORIES) | {OTHERS_DIR, EXTRACTED_DIR, TRASH_DIR}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def unique_path(path: Path) -> Path:
    """Return `path`, or 'name (1).ext', 'name (2).ext', ... if it is taken."""
    if not path.exists():
        return path
    for i in count(1):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("unreachable")


def ask(question: str) -> bool:
    """Ask a yes/no question. Anything but yes counts as no."""
    try:
        answer = input(f"{question} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def is_protected(folder: Path) -> bool:
    """True for drive roots, the home folder itself and system folders."""
    if folder == Path(folder.anchor) or folder == Path.home().resolve():
        return True
    for raw in SYSTEM_DIRS:
        system_dir = Path(raw)
        if system_dir.is_absolute() and folder.is_relative_to(system_dir):
            return True
    return False


def write_log(root: Path, message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line)
    with open(root / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_journal(root: Path) -> list[dict]:
    try:
        data = json.loads((root / JOURNAL_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_journal(root: Path, runs: list[dict]) -> None:
    path = root / JOURNAL_NAME
    if runs:
        path.write_text(json.dumps(runs[-MAX_UNDO_RUNS:], indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# The organizer
# ---------------------------------------------------------------------------

class Organizer:
    def __init__(self, root: Path, dry_run: bool, interactive: bool) -> None:
        self.root = root
        self.dry_run = dry_run
        self.interactive = interactive
        self.moves: list[dict[str, str]] = []   # journal entries (relative paths)
        self.planned = 0                        # moves counted in a dry run
        self.merge_candidates = 0
        self.errors = 0
        self._listing_cache: dict[Path, set[str]] = {}

    # -- output -------------------------------------------------------------

    def log(self, message: str) -> None:
        if self.dry_run:
            print(message)
        else:
            write_log(self.root, message)

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    # -- filtering ----------------------------------------------------------

    def should_skip(self, path: Path) -> bool:
        name = path.name
        if name.startswith("."):                 # hidden, e.g. .git
            return True
        if name.lower() in IGNORED_NAMES or name in {LOG_NAME, JOURNAL_NAME}:
            return True
        if path.is_symlink():                    # leave links alone
            return True
        if path.resolve() == SCRIPT_PATH:        # don't move ourselves
            return True
        if path.is_file() and path.suffix.lower() in PARTIAL_DOWNLOAD_EXTS:
            return True
        return False

    # -- moving -------------------------------------------------------------

    def move_to(self, src: Path, dest: Path) -> None:
        """Move `src` to the exact path `dest` (which must not exist)."""
        if self.dry_run:
            print(f"  {self.rel(src)}  ->  {self.rel(dest)}")
            self.planned += 1
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        except OSError as e:   # e.g. file open in another program
            self.errors += 1
            self.log(f"ERROR moving {self.rel(src)}: {e}")
            return
        self.moves.append({"from": self.rel(src), "to": self.rel(dest)})
        self.log(f"MOVED {self.rel(src)} -> {self.rel(dest)}")

    def move_into(self, src: Path, folder: Path) -> None:
        """Move `src` into `folder`, renaming it if the name is taken."""
        self.move_to(src, unique_path(folder / src.name))

    # -- step 1: loose files -----------------------------------------------

    def organize_files(self) -> None:
        for file in sorted(self.root.iterdir()):
            if not file.is_file() or self.should_skip(file):
                continue
            category = EXTENSION_MAP.get(file.suffix.lower(), OTHERS_DIR)
            self.move_into(file, self.root / category)

    # -- step 2: loose folders ---------------------------------------------

    def files_in(self, folder: Path) -> set[str]:
        if folder not in self._listing_cache:
            self._listing_cache[folder] = {
                f.relative_to(folder).as_posix()
                for f in folder.rglob("*") if f.is_file()
            }
        return self._listing_cache[folder]

    def similarity(self, a: Path, b: Path) -> float:
        files_a, files_b = self.files_in(a), self.files_in(b)
        if not files_a or not files_b:
            return 0.0
        return len(files_a & files_b) / max(len(files_a), len(files_b))

    def find_match(self, folder: Path, extracted: Path) -> tuple[Path | None, float]:
        if not extracted.is_dir():
            return None, 0.0
        best, best_score = None, 0.0
        for existing in extracted.iterdir():
            if not existing.is_dir() or self.should_skip(existing):
                continue
            same_name = existing.name.lower() == folder.name.lower()
            name_score = 1.0 if same_name else DIFFERENT_NAME_SCORE
            score = (NAME_WEIGHT * name_score
                     + CONTENT_WEIGHT * self.similarity(folder, existing))
            if score > best_score:
                best, best_score = existing, score
        return best, best_score

    def merge(self, source: Path, target: Path) -> None:
        """Move files from `source` into `target`, never overwriting."""
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            dest = target / item.relative_to(source)
            if dest.exists():
                self.log(f"  KEPT EXISTING {self.rel(dest)} "
                         f"(the copy goes to {TRASH_DIR}/)")
                continue
            self.move_to(item, dest)
        self._listing_cache.pop(target, None)

    def organize_folders(self) -> None:
        extracted = self.root / EXTRACTED_DIR
        for folder in sorted(self.root.iterdir()):
            if (not folder.is_dir() or folder.name in RESERVED_DIRS
                    or self.should_skip(folder)):
                continue

            match, score = self.find_match(folder, extracted)
            if match is None or score < MERGE_THRESHOLD:
                self.move_into(folder, extracted)
                continue

            self.merge_candidates += 1
            self.log(f"POSSIBLE DUPLICATE: '{folder.name}' looks like "
                     f"'{EXTRACTED_DIR}/{match.name}' ({score:.0%} match)")
            if self.dry_run:
                print("  (you'll be asked whether to merge them)")
                continue
            if not self.interactive or not ask("  Merge them?"):
                self.log("  Not merged; folder left where it is.")
                continue

            self.merge(folder, match)
            # Whatever is left (duplicates, empty subfolders) goes to _trash.
            self.move_into(folder, self.root / TRASH_DIR)

    def run(self) -> None:
        self.organize_files()
        self.organize_folders()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_organize(root: Path, dry_run: bool, assume_yes: bool, interactive: bool) -> None:
    print(f"Folder: {root}\n\nPlanned changes:")
    preview = Organizer(root, dry_run=True, interactive=False)
    preview.run()

    if preview.planned == 0 and preview.merge_candidates == 0:
        print("  none. The folder is already tidy.")
        return
    print(f"\n{preview.planned} item(s) to move, "
          f"{preview.merge_candidates} possible duplicate folder(s).")
    if dry_run:
        print("Dry run: nothing was changed.")
        return
    if not assume_yes:
        if not interactive:
            print("Not running interactively. Re-run with --yes to apply.")
            return
        if not ask("\nApply these changes?"):
            print("Cancelled. Nothing was changed.")
            return

    organizer = Organizer(root, dry_run=False, interactive=interactive)
    write_log(root, "=== START RUN ===")
    try:
        organizer.run()
    finally:
        # Save the journal even if something crashed, so --undo still works.
        if organizer.moves:
            runs = load_journal(root)
            runs.append({"time": datetime.now().isoformat(timespec="seconds"),
                         "moves": organizer.moves})
            save_journal(root, runs)
        write_log(root, f"=== END RUN: {len(organizer.moves)} moved, "
                        f"{organizer.errors} error(s) ===")
    if organizer.moves:
        print(f"\nDone. To reverse this run: python {SCRIPT_PATH.name} --undo")


def remove_empty_dirs(root: Path, folders: set[Path]) -> None:
    """Remove now-empty folders left behind by an undo (never `root`)."""
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        while folder != root and folder.is_relative_to(root):
            try:
                folder.rmdir()   # only succeeds if empty
            except OSError:
                break
            folder = folder.parent


def cmd_undo(root: Path, dry_run: bool) -> None:
    runs = load_journal(root)
    if not runs:
        print("Nothing to undo.")
        return
    last = runs[-1]
    print(f"Undoing the run from {last['time']} ({len(last['moves'])} moves)\n")

    restored, skipped, emptied = 0, 0, set()
    for move in reversed(last["moves"]):
        original, current = root / move["from"], root / move["to"]
        if not current.exists():
            print(f"  skipped (no longer there): {move['to']}")
            skipped += 1
            continue
        if original.exists():
            print(f"  skipped (something is already at {move['from']})")
            skipped += 1
            continue
        if dry_run:
            print(f"  {move['to']}  ->  {move['from']}")
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(original))
        emptied.add(current.parent)
        restored += 1

    if dry_run:
        print("\nDry run: nothing was changed.")
        return
    remove_empty_dirs(root, emptied)
    save_journal(root, runs[:-1])
    write_log(root, f"=== UNDO: {restored} restored, {skipped} skipped ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sort a folder's files into category subfolders.")
    parser.add_argument("folder", nargs="?",
                        help="folder to organize (default: the folder this script is in)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only show what would happen")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="apply without the preview question")
    parser.add_argument("--undo", action="store_true",
                        help="reverse the last run")
    parser.add_argument("--force", action="store_true",
                        help="allow home/system folders (not recommended)")
    parser.add_argument("--no-pause", action="store_true",
                        help="don't wait for Enter at the end (Windows)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = (Path(args.folder).expanduser() if args.folder else SCRIPT_PATH.parent).resolve()
    interactive = sys.stdin.isatty()

    try:
        if not root.is_dir():
            print(f"Not a folder: {root}")
            return 1
        if is_protected(root) and not args.force:
            print(f"Refusing to organize {root}: it is a home, drive or system "
                  "folder. Use --force if you are really sure.")
            return 1
        if args.undo:
            cmd_undo(root, args.dry_run)
        else:
            cmd_organize(root, args.dry_run, args.yes, interactive)
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    finally:
        # Keep the window open when double-clicked on Windows.
        if os.name == "nt" and interactive and not args.no_pause:
            input("\nPress Enter to exit...")


if __name__ == "__main__":
    sys.exit(main())