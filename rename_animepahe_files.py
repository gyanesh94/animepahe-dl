"""Rename anime episode video files recursively.

Rules
1) If a filename starts with "AnimePahe", apply AnimePahe cleanup:
   - Remove the leading "AnimePahe" token.
   - Replace "_" with spaces.
   - Collapse multiple spaces into one.
   - Normalize "Eng Dub" -> "(Eng)".
   - Truncate everything after the episode number.

2) After cleanup (or for non-AnimePahe names), zero-pad the *episode number* only
   when it is the trailing numeric token in the filename stem (i.e., immediately
   before the file extension). Existing leading zeros are preserved.

   Examples:
   - "Show Name 1.mp4" -> "Show Name 01.mp4"
   - "Show Name 019.mp4" stays "Show Name 019.mp4"
   - "019. The Tournament Begins.mp4" stays unchanged (number is not trailing)

Traversal
- Recursively scans all subdirectories under --root.
- Renames only common video extensions: .mp4, .mkv, .avi, .mov, .m4v, .webm, .wmv, .ts
- You can skip directories (and their subtrees) by providing one or more ignore tags.
  Any directory whose name contains a tag substring will be skipped.

Examples (AnimePahe)
- "AnimePahe_Shigatsu_wa_Kimi_no_Uso_-_Moments_-_01_BD_1080p_Asakura.mp4"
  -> "Shigatsu wa Kimi no Uso - Moments - 01.mp4"
- "AnimePahe_Shigatsu_wa_Kimi_no_Uso_-_01_BD_1080p_SumiSora-CASO.mp4"
  -> "Shigatsu wa Kimi no Uso - 01.mp4"
- "AnimePahe_Dragon_Ball_Z_Movie_01_-_Ora_no_Gohan_wo_Kaese_Eng_Dub_-_01_BD_1080p_a-S.mp4"
  -> "Dragon Ball Z Movie 01 - Ora no Gohan wo Kaese (Eng) - 01.mp4"

Run
- Dry run (full paths):
  python rename_animepahe_files.py --root ./ --dry-run

- Dry run (filenames only):
  python rename_animepahe_files.py --root ./ --dry-run --name-only

- Skip directories containing a tag (repeatable):
  python rename_animepahe_files.py --root ./ --dry-run --ignore-dir-tag "[skip]" --ignore-dir-tag "_ignore_"

- Execute:
  python rename_animepahe_files.py --root ./

Run tests
  pytest -q rename_animepahe_files.py
"""

from __future__ import annotations

import argparse
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional


_ANIMEPAHE_PREFIX = "AnimePahe"

_VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".ts",
    ".webm",
    ".wmv",
}

# Common tokens that usually appear after the episode number in AnimePahe names.
_QUALITY_TOKENS = (
    "BD",
    "WEB",
    "DVD",
    "BluRay",
    "HDRip",
    "HEVC",
    "x264",
    "x265",
    "10bit",
    "8bit",
    "1080p",
    "720p",
    "480p",
    "540p",
    "360p",
)


@dataclass(frozen=True)
class RenameOp:
    """A single rename operation."""

    src: Path
    dst: Path


def normalize_spaces(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""

    return re.sub(r"\s+", " ", text).strip()


def clean_animepahe_stem(stem: str) -> str:
    """Clean an AnimePahe filename stem (without extension).

    Steps:
    - Remove leading "AnimePahe".
    - Replace underscores with spaces.
    - Normalize whitespace.
    - Normalize "Eng Dub" -> "(Eng)".
    - Truncate everything after the episode number.

    Args:
        stem: Filename stem (no extension).

    Returns:
        Cleaned stem.
    """

    # Remove the prefix and any separators immediately following it.
    cleaned = re.sub(rf"^{re.escape(_ANIMEPAHE_PREFIX)}[_\s-]*", "", stem)

    # Replace underscores with spaces (AnimePahe uses underscores as delimiters).
    cleaned = cleaned.replace("_", " ")
    cleaned = normalize_spaces(cleaned)

    # Convert "Eng Dub" to "(Eng)". Keep it conservative.
    cleaned = re.sub(r"\bEng\s+Dub\b", "(Eng)", cleaned, flags=re.IGNORECASE)
    cleaned = normalize_spaces(cleaned)

    # Truncate after the *episode* number. Prefer the number that appears immediately
    # before a known quality token.
    quality_alt = "|".join(map(re.escape, _QUALITY_TOKENS))

    # Capture the episode number as the last such occurrence.
    pattern = re.compile(
        rf"(?P<prefix>.*?)(?P<ep>\d{{1,4}})(?=\s+(?:{quality_alt})\b)",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(cleaned))
    if matches:
        last = matches[-1]
        return cleaned[: last.end("ep")].strip()

    # Fallback: truncate after the last standalone number.
    last_num = re.search(r"(\d+)(?!.*\d)", cleaned)
    if last_num:
        cleaned = cleaned[: last_num.end(1)].strip()

    return cleaned


def extract_trailing_int(text: str) -> Optional[int]:
    """Extract an integer only if it appears at the end of the string.

    This treats the episode number as the trailing numeric token in the stem.

    Args:
        text: Any string.

    Returns:
        The trailing integer, or None if the string does not end with digits.
    """

    match = re.search(r"(\d+)\s*$", text)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def replace_trailing_int_with_zfill(text: str, width: int) -> str:
    """Zero-pad a trailing integer in text.

    Only applies if the string ends with digits (optionally followed by spaces).
    Preserves existing leading zeros; it will only increase the width when needed
    and will never reduce the number of digits.
    """

    match = re.search(r"(\d+)(\s*)$", text)
    if not match:
        return text

    digits = match.group(1)
    trailing_ws = match.group(2)
    padded = digits if len(digits) >= width else digits.zfill(width)
    return f"{text[:match.start(1)]}{padded}{trailing_ws}"


def determine_episode_width(episode_numbers: Iterable[int]) -> int:
    """Determine consistent zero-padding width for a directory.

    Minimum width is 2.
    """

    nums = list(episode_numbers)
    if not nums:
        return 2

    max_num = max(nums)
    return max(2, len(str(max_num)))


def should_ignore_directory(path: Path, ignore_dir_tags: list[str]) -> bool:
    """Return True if any ignore tag is present in any directory component."""

    if not ignore_dir_tags:
        return False

    return any(
        any(tag in part for tag in ignore_dir_tags)
        for part in path.parts
    )


def iter_video_files_by_directory(
    root: Path,
    *,
    ignore_dir_tags: Optional[list[str]] = None,
) -> Iterator[tuple[Path, list[Path]]]:
    """Yield (directory, video_files_in_directory) for all directories under root.

    If `ignore_dir_tags` is provided, any directory whose name contains one of the
    tags will be skipped, and its subtree will not be traversed.
    """

    ignore_dir_tags = ignore_dir_tags or []

    for dirpath, dirnames, filenames in os.walk(root):
        directory = Path(dirpath)

        # Prune traversal into ignored subdirectories.
        if ignore_dir_tags:
            dirnames[:] = [
                d for d in dirnames if not any(tag in d for tag in ignore_dir_tags)
            ]

        if should_ignore_directory(directory, ignore_dir_tags):
            continue

        files: list[Path] = []

        for name in filenames:
            if name.startswith("."):
                continue

            path = directory / name
            if path.suffix.lower() in _VIDEO_EXTENSIONS:
                files.append(path)

        if files:
            yield directory, files


def build_rename_ops(
    root: Path,
    *,
    ignore_dir_tags: Optional[list[str]] = None,
) -> list[RenameOp]:
    """Build rename operations for all video files under root."""

    ops: list[RenameOp] = []
    ignore_dir_tags = ignore_dir_tags or []

    for _, files in iter_video_files_by_directory(root, ignore_dir_tags=ignore_dir_tags):
        planned: list[tuple[Path, str, Optional[int]]] = []

        for path in files:
            stem = path.stem
            if stem.startswith(_ANIMEPAHE_PREFIX):
                stem = clean_animepahe_stem(stem)

            ep_num = extract_trailing_int(stem)
            planned.append((path, stem, ep_num))

        width = determine_episode_width(ep for _, _, ep in planned if ep is not None)

        for src, cleaned_stem, ep_num in planned:
            new_stem = cleaned_stem
            if ep_num is not None:
                new_stem = replace_trailing_int_with_zfill(cleaned_stem, width)

            dst = src.with_name(f"{new_stem}{src.suffix}")
            if dst != src:
                ops.append(RenameOp(src=src, dst=dst))

    _validate_no_destination_collisions(ops)
    return ops


def _validate_no_destination_collisions(ops: list[RenameOp]) -> None:
    """Ensure no two sources map to the same destination."""

    seen: dict[Path, Path] = {}
    for op in ops:
        if op.dst in seen and seen[op.dst] != op.src:
            raise RuntimeError(
                "Multiple files would be renamed to the same destination: "
                f"{seen[op.dst]} and {op.src} -> {op.dst}"
            )
        seen[op.dst] = op.src


def format_rename_op(op: RenameOp, *, name_only: bool) -> str:
    """Format a rename operation for display."""

    if name_only:
        return f"DRY-RUN: {op.src.name} -> {op.dst.name}"

    return f"DRY-RUN: {op.src} -> {op.dst}"


def apply_rename_ops(
    ops: list[RenameOp],
    *,
    dry_run: bool,
    name_only: bool,
) -> None:
    """Apply rename operations safely.

    Uses a two-phase rename via temporary filenames when needed to avoid
    in-directory collisions.
    """

    if dry_run:
        for op in ops:
            print(format_rename_op(op, name_only=name_only))
        return

    src_set = {op.src for op in ops}

    # Fail fast if destination exists and is not part of the batch.
    for op in ops:
        if op.dst.exists() and op.dst not in src_set:
            raise FileExistsError(f"Destination already exists: {op.dst}")

    # First pass: rename any src to temp if its dst is also a source path.
    temp_ops: list[tuple[Path, Path]] = []
    final_ops: list[tuple[Path, Path]] = []

    for op in ops:
        if op.dst in src_set:
            temp = op.src.with_name(f"{op.src.name}.tmp-rename-{uuid.uuid4().hex}")
            temp_ops.append((op.src, temp))
            final_ops.append((temp, op.dst))
        else:
            final_ops.append((op.src, op.dst))

    for src, tmp in temp_ops:
        src.rename(tmp)

    for src, dst in final_ops:
        src.rename(dst)


def rename_files_recursively(
    root: Path,
    *,
    dry_run: bool = False,
    name_only: bool = False,
    ignore_dir_tags: Optional[list[str]] = None,
) -> list[RenameOp]:
    """Plan and apply renames under root."""

    ops = build_rename_ops(root, ignore_dir_tags=ignore_dir_tags)
    apply_rename_ops(ops, dry_run=dry_run, name_only=name_only)
    return ops


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=str,
        default="./",
        help="Root directory to scan recursively (default: ./)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames without changing files",
    )
    parser.add_argument(
        "--name-only",
        action="store_true",
        help="With --dry-run, print only filenames (no full paths)",
    )
    parser.add_argument(
        "--ignore-dir-tag",
        action="append",
        default=[],
        help=(
            "Skip any directory (and its subtree) whose name contains this tag. "
            "Repeatable. Example: --ignore-dir-tag [skip]"
        ),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    root = Path(args.root).expanduser().resolve()
    rename_files_recursively(
        root,
        dry_run=args.dry_run,
        name_only=args.name_only,
        ignore_dir_tags=args.ignore_dir_tag,
    )


if __name__ == "__main__":
    main()


# ----------------------------
# Pytest unit tests (run with):
#   pytest -q rename_animepahe_files.py
# ----------------------------


def test_clean_animepahe_stem_examples() -> None:
    assert (
        clean_animepahe_stem(
            "AnimePahe_Shigatsu_wa_Kimi_no_Uso_-_Moments_-_01_BD_1080p_Asakura"
        )
        == "Shigatsu wa Kimi no Uso - Moments - 01"
    )

    assert (
        clean_animepahe_stem(
            "AnimePahe_Shigatsu_wa_Kimi_no_Uso_-_01_BD_1080p_SumiSora-CASO"
        )
        == "Shigatsu wa Kimi no Uso - 01"
    )

    assert (
        clean_animepahe_stem(
            "AnimePahe_Dragon_Ball_Z_Movie_01_-_Ora_no_Gohan_wo_Kaese_Eng_Dub_-_01_BD_1080p_a-S"
        )
        == "Dragon Ball Z Movie 01 - Ora no Gohan wo Kaese (Eng) - 01"
    )


def test_episode_padding_min_two_digits() -> None:
    assert replace_trailing_int_with_zfill("Ameku M_D__ Doctor Detective 1", 2) == (
        "Ameku M_D__ Doctor Detective 01"
    )


def test_build_rename_ops_zero_pads_per_directory(tmp_path: Path) -> None:
    d = tmp_path / "show"
    d.mkdir()

    f1 = d / "Ameku M_D__ Doctor Detective 1.mp4"
    f2 = d / "Ameku M_D__ Doctor Detective 10.mp4"
    f1.write_bytes(b"")
    f2.write_bytes(b"")

    ops = build_rename_ops(tmp_path)

    assert any(op.src == f1 and op.dst.name.endswith("01.mp4") for op in ops)
    assert not any(op.src == f2 for op in ops)


def test_format_rename_op_name_only(tmp_path: Path) -> None:
    src = tmp_path / "a" / "Ep 1.mp4"
    dst = tmp_path / "a" / "Ep 01.mp4"
    op = RenameOp(src=src, dst=dst)

    assert format_rename_op(op, name_only=True) == "DRY-RUN: Ep 1.mp4 -> Ep 01.mp4"
    assert str(src) in format_rename_op(op, name_only=False)
    assert str(dst) in format_rename_op(op, name_only=False)


def test_episode_padding_only_applies_to_trailing_number() -> None:
    assert (
        replace_trailing_int_with_zfill("019. The Tournament Begins", 2)
        == "019. The Tournament Begins"
    )
    assert (
        replace_trailing_int_with_zfill("019. The Tournament Begins", 4)
        == "019. The Tournament Begins"
    )
    assert replace_trailing_int_with_zfill("Episode 019", 2) == "Episode 019"
    assert replace_trailing_int_with_zfill("Episode 19", 2) == "Episode 19"


def test_ignore_dir_tag_skips_subtree(tmp_path: Path) -> None:
    keep_dir = tmp_path / "keep"
    skip_dir = tmp_path / "[skip]"
    keep_dir.mkdir()
    skip_dir.mkdir()

    keep_file = keep_dir / "Show 1.mp4"
    skip_file = skip_dir / "Show 1.mp4"
    keep_file.write_bytes(b"")
    skip_file.write_bytes(b"")

    ops = build_rename_ops(tmp_path, ignore_dir_tags=["[skip]"])

    assert any(op.src == keep_file for op in ops)
    assert not any(op.src == skip_file for op in ops)