from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CleanupResult:
    screenshots_deleted: int = 0
    log_backups_deleted: int = 0

    @property
    def message(self) -> str:
        return f"已清理截图 {self.screenshots_deleted} 张，日志备份 {self.log_backups_deleted} 个"


def cleanup_runtime_files(
    screenshots_dir: Path = Path("screenshots_debug"),
    logs_dir: Path = Path("logs"),
    keep_screenshots: int = 80,
    keep_log_backups: int = 5,
) -> CleanupResult:
    return CleanupResult(
        screenshots_deleted=cleanup_screenshots(screenshots_dir, keep_screenshots),
        log_backups_deleted=cleanup_log_backups(logs_dir, keep_log_backups),
    )


def cleanup_screenshots(path: Path = Path("screenshots_debug"), keep: int = 80) -> int:
    if keep < 0 or not path.exists():
        return 0
    files = [item for item in path.glob("*.png") if item.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    deleted = 0
    for item in files[keep:]:
        try:
            item.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted


def cleanup_log_backups(path: Path = Path("logs"), keep: int = 5) -> int:
    if keep < 0 or not path.exists():
        return 0
    files = [item for item in path.glob("app.log.*") if item.is_file()]
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    deleted = 0
    for item in files[keep:]:
        try:
            item.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted
