# Folder Organizer

A single Python file that tidies any messy folder (Downloads, Desktop, a USB stick...) by sorting files into category folders. It shows you a preview first, never overwrites or deletes anything, and every run can be undone.

```
Before                     After
Downloads/                 Downloads/
├── photo.jpg              ├── Images/photo.jpg
├── report.pdf             ├── Documents/report.pdf
├── song.mp3               ├── Audio/song.mp3
├── setup.exe              ├── Executables/setup.exe
└── project/               └── Extracted Folders/project/
```

## Usage

Requires Python 3.9 or newer. No extra packages.

1. Put `organize.py` in the folder you want to clean up.
2. Run it (double-click on Windows, or `python organize.py` in a terminal).
3. Check the preview and type `y` to apply.

| Command | What it does |
| --- | --- |
| `python organize.py` | Organize the folder the script is in |
| `python organize.py ~/Downloads` | Organize a different folder |
| `python organize.py --dry-run` | Only show what would happen |
| `python organize.py --yes` | Skip the preview question |
| `python organize.py --undo` | Put everything back as it was before the last run |

## Safety

- **Preview first.** Nothing changes until you confirm.
- **No overwrites.** If a file with the same name exists, the new one becomes `name (1).ext`.
- **No deletions.** Leftovers from a folder merge go to `_trash/` for you to check and delete yourself.
- **Undo.** The last 10 runs are recorded in `.organizer_journal.json`.
- **Leaves alone:** hidden files and folders (like `.git`), unfinished downloads (`.crdownload`, `.part`, ...), shortcuts/symlinks, and system files like `desktop.ini`.
- **Refuses** to run on your home folder, a drive root, or system folders (unless you add `--force`).

Don't run it inside a code project or any folder whose layout matters to a program.

## Categories

Images, Videos, Audio, Documents, Spreadsheets, Presentations, eBooks, Compressed, Disk Images, Executables, Code, Fonts, 3D Models, Torrents, and Others for anything unknown. Edit the `CATEGORIES` list at the top of `organize.py` to add your own.

## Duplicate folders

Loose folders go into `Extracted Folders/`. If one looks like a folder already there (same name or mostly the same files), you'll be asked whether to merge them. Files that already exist in the target are kept; the duplicates go to `_trash/`.

## License

MIT – see [LICENSE](LICENSE).
