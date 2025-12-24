"""Rename anime episode video files recursively.

Rules:
1) If a filename starts with "AnimePahe", apply AnimePahe cleanup:
   - Remove the leading "AnimePahe" token.
   - Replace underscores with spaces.
   - Collapse repeated whitespace.
   - Normalize "Eng Dub" -> "(Eng)".
   - Truncate everything after the episode number.

2) After cleanup (or for non-AnimePahe names), zero-pad the last episode number
   in each directory to a consistent width (minimum 2 digits).

The renaming is performed recursively for all subdirectories for common video extensions.

Examples:
- "AnimePahe_Shigatsu_wa_Kimi_no_Uso_-_Moments_-_01_BD_1080p_Asakura.mp4"
  -> "Shigatsu wa Kimi no Uso - Moments - 01.mp4"
- "AnimePahe_Dragon_Ball_Z_Movie_01_-_Ora_no_Gohan_wo_Kaese_Eng_Dub_-_01_BD_1080p_a-S.mp4"
  -> "Dragon Ball Z Movie 01 - Ora no Gohan wo Kaese (Eng) - 01.mp4"
- "Ameku M_D__ Doctor Detective 1.mp4"
  -> "Ameku M_D__ Doctor Detective 01.mp4"

Run:
  python rename_animepahe_files.py --root ./ --dry-run
  python rename_animepahe_files.py --root ./

Run tests:
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
        cleaned = (cleaned[: last.end("ep")]).strip()
        return cleaned

    # Fallback: if no quality token match, truncate after the last standalone number.
    last_num = re.search(r"(\d+)(?!.*\d)", cleaned)
    if last_num:
        cleaned = cleaned[: last_num.end(1)].strip()

    return cleaned


def extract_last_int(text: str) -> Optional[int]:
    """Extract the last integer in a string.

    Args:
        text: Any string.

    Returns:
        The last integer found, or None if no digits are present.
    """

    match = re.search(r"(\d+)(?!.*\d)", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def replace_last_int_with_zfill(text: str, width: int) -> str:
    """Replace the last integer in text with a zero-filled version."""

    match = re.search(r"(\d+)(?!.*\d)", text)
    if not match:
        return text

    number = int(match.group(1))
    padded = str(number).zfill(width)
    return f"{text[:match.start(1)]}{padded}{text[match.end(1):]}"


def determine_episode_width(episode_numbers: Iterable[int]) -> int:
    """Determine consistent zero-padding width for a directory.

    Minimum width is 2.
    """

    nums = list(episode_numbers)
    if not nums:
        return 2
    max_num = max(nums)
    return max(2, len(str(max_num)))


def iter_video_files_by_directory(root: Path) -> Iterator[tuple[Path, list[Path]]]:
    """Yield (directory, video_files_in_directory) for all directories under root."""

    for dirpath, _, filenames in os.walk(root):
        directory = Path(dirpath)
        files: list[Path] = []

        for name in filenames:
            if name.startswith("."):
                continue

            path = directory / name
            if path.suffix.lower() in _VIDEO_EXTENSIONS:
                files.append(path)

        if files:
            yield directory, files


def build_rename_ops(root: Path) -> list[RenameOp]:
    """Build rename operations for all mp4 files under root."""

    ops: list[RenameOp] = []

    for directory, files in iter_video_files_by_directory(root):
        planned: list[tuple[Path, str, Optional[int]]] = []

        for path in files:
            stem = path.stem
            if stem.startswith(_ANIMEPAHE_PREFIX):
                stem = clean_animepahe_stem(stem)

            ep_num = extract_last_int(stem)
            planned.append((path, stem, ep_num))

        width = determine_episode_width(
            ep for _, _, ep in planned if ep is not None
        )

        for src, cleaned_stem, ep_num in planned:
            new_stem = cleaned_stem
            if ep_num is not None:
                new_stem = replace_last_int_with_zfill(cleaned_stem, width)

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


def apply_rename_ops(ops: list[RenameOp], *, dry_run: bool) -> None:
    """Apply rename operations safely.

    Uses a two-phase rename via temporary filenames when needed to avoid
    in-directory collisions.
    """

    if dry_run:
        for op in ops:
            print(f"DRY-RUN: {op.src} -> {op.dst}")
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


def rename_files_recursively(root: Path, *, dry_run: bool = False) -> list[RenameOp]:
    """Plan and apply renames under root."""

    ops = build_rename_ops(root)
    apply_rename_ops(ops, dry_run=dry_run)
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
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    root = Path(args.root).expanduser().resolve()
    rename_files_recursively(root, dry_run=args.dry_run)


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
    assert replace_last_int_with_zfill("Ameku M_D__ Doctor Detective 1", 2) == (
        "Ameku M_D__ Doctor Detective 01"
    )


def test_build_rename_ops_zero_pads_per_directory(tmp_path: Path) -> None:
    d = tmp_path / "show"
    d.mkdir()

    # Create files that should become 01 and 10.
    f1 = d / "Ameku M_D__ Doctor Detective 1.mp4"
    f2 = d / "Ameku M_D__ Doctor Detective 10.mp4"
    f1.write_bytes(b"")
    f2.write_bytes(b"")

    ops = build_rename_ops(tmp_path)

    # Exactly one op for f1 (f2 already OK for width=2).
    assert any(op.src == f1 and op.dst.name.endswith("01.mp4") for op in ops)
    assert not any(op.src == f2 for op in ops)