"""Fail-closed filesystem checks for in-repository records and inputs.

Operator CLIs and workflow boundaries use these helpers before reading or
creating provenance records and declared inputs. Lexically in-repository paths
are not enough: a symlinked parent or final file could otherwise redirect state
or scientific bytes outside the checkout.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
from collections.abc import Iterator
from io import BufferedReader
from pathlib import Path


def _rooted(root: Path, path: Path, *, label: str) -> tuple[Path, Path, Path]:
    """Return resolved root, absolute candidate, and safe lexical relative path."""
    resolved_root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else resolved_root / path
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the repository: {path}") from exc
    if ".." in relative.parts:
        raise ValueError(f"{label} is not a canonical repository path: {path}")
    return resolved_root, candidate, relative


def ensure_real_directory(
    root: Path, path: Path, *, label: str, create: bool = False
) -> Path:
    """Require every directory component to be real, in-repository, and non-symlink."""
    resolved_root, candidate, relative = _rooted(root, path, label=label)
    current = resolved_root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlinked directory: {current}")
        if create:
            try:
                current.mkdir()
            except FileExistsError:
                pass
        if not current.exists() or not current.is_dir():
            raise ValueError(f"{label} is not a real directory: {current}")
        try:
            current.resolve(strict=True).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(
                f"{label} resolves outside the repository: {current}"
            ) from exc
    return candidate


def require_regular_file(root: Path, path: Path, *, label: str) -> Path:
    """Require a non-symlink regular file below real in-repository parents."""
    resolved_root, candidate, _ = _rooted(root, path, label=label)
    ensure_real_directory(resolved_root, candidate.parent, label=label)
    if candidate.is_symlink():
        raise ValueError(f"{label} must be a regular file, not a symlink: {candidate}")
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {candidate}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file: {candidate}")
    return candidate


@contextlib.contextmanager
def open_binary_no_follow(
    root: Path, path: Path, *, label: str
) -> Iterator[BufferedReader]:
    """Open one canonical regular file without following its final symlink."""
    candidate = require_regular_file(root, path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as exc:
        raise ValueError(f"cannot safely open {label} {candidate}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{label} must be a regular file: {candidate}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            yield handle
    finally:
        if fd >= 0:
            os.close(fd)


def read_bytes_no_follow(root: Path, path: Path, *, label: str) -> bytes:
    """Read one canonical regular file without following a final symlink."""
    with open_binary_no_follow(root, path, label=label) as handle:
        return handle.read()


def read_text_no_follow(root: Path, path: Path, *, label: str) -> str:
    """Read UTF-8 text through the no-follow regular-file boundary."""
    return read_bytes_no_follow(root, path, label=label).decode("utf-8")


def require_clean_dvc_outputs(root: Path, paths: list[Path], *, label: str) -> None:
    """Require real raw payloads to have clean, committed adjacent DVC pointers."""
    if not paths:
        return
    resolved_root = root.resolve(strict=True)
    relative: list[str] = []
    git_metadata: list[str] = []
    for path in paths:
        candidate = require_regular_file(resolved_root, path, label=label)
        relative.append(candidate.relative_to(resolved_root).as_posix())
        pointer = require_regular_file(
            resolved_root,
            candidate.with_name(f"{candidate.name}.dvc"),
            label=f"{label} DVC pointer",
        )
        ignore = require_regular_file(
            resolved_root,
            candidate.parent / ".gitignore",
            label=f"{label} DVC ignore metadata",
        )
        git_metadata.extend(
            (
                pointer.relative_to(resolved_root).as_posix(),
                ignore.relative_to(resolved_root).as_posix(),
            )
        )
    git_metadata = sorted(set(git_metadata))
    try:
        tracked = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "ls-files",
                "--error-unmatch",
                "--",
                *git_metadata,
            ],
            cwd=resolved_root,
            capture_output=True,
            text=True,
            check=False,
        )
        clean = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                *git_metadata,
            ],
            cwd=resolved_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} requires Git to verify DVC metadata") from exc
    if tracked.returncode != 0 or clean.returncode != 0:
        detail = (tracked.stderr or clean.stderr or "untracked or modified").strip()
        raise ValueError(
            f"{label} requires committed, unmodified adjacent DVC metadata "
            f"{git_metadata}: {detail}"
        )
    try:
        raw_tracked = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "ls-files",
                "-z",
                "--cached",
                "--",
                *relative,
            ],
            cwd=resolved_root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} requires Git to verify raw storage") from exc
    if raw_tracked.returncode != 0:
        detail = (raw_tracked.stderr or b"unknown Git error").decode(errors="replace")
        raise RuntimeError(f"cannot verify raw Git storage: {detail.strip()}")
    tracked_raw = [
        os.fsdecode(item) for item in raw_tracked.stdout.split(b"\0") if item
    ]
    if tracked_raw:
        raise ValueError(
            f"{label} payloads must be DVC-only and not tracked by Git: {tracked_raw}"
        )
    for path in relative:
        ignored = subprocess.run(
            [
                "git",
                "check-ignore",
                "--no-index",
                "-q",
                "--",
                path,
            ],
            cwd=resolved_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if ignored.returncode == 1:
            raise ValueError(
                f"{label} payload must be ignored by its committed DVC rule: {path}"
            )
        if ignored.returncode != 0:
            detail = (ignored.stderr or ignored.stdout or "unknown Git error").strip()
            raise RuntimeError(f"cannot verify raw ignore policy for {path}: {detail}")
    try:
        process = subprocess.run(
            ["dvc", "status", "--json", *relative],
            cwd=resolved_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{label} requires DVC, but the dvc executable is unavailable"
        ) from exc
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "not a DVC output").strip()
        raise ValueError(f"{label} must be tracked by DVC: {relative}; {detail}")
    try:
        status = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DVC returned invalid status JSON: {exc}") from exc
    if not isinstance(status, dict):
        raise RuntimeError("DVC status JSON must be an object")
    if status:
        raise ValueError(
            f"{label} must match its committed DVC metadata; status={status}"
        )
