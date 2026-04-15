# C:\EA_AI\tools\sync_jsonl_from_mt5.py

import os
import glob
import shutil
from pathlib import Path

from dotenv import load_dotenv

# -------------------- إعداد المسارات من .env --------------------

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"

if ENV_PATH.exists():
    load_dotenv(str(ENV_PATH))

MT5_COMMON_FILES = os.getenv("MT5_COMMON_FILES", "").strip()
JSONL_DIR = os.getenv("JSONL_DIR", str(PROJECT_ROOT / "runtime" / "logs")).strip()
JSONL_FILE_PREFIX = os.getenv("JSONL_FILE_PREFIX", "trades_").strip()


def log(msg: str) -> None:
    print(f"[sync_jsonl] {msg}")


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def find_source_files() -> list:
    """
    نبحث عن ملفات trades_*.jsonl في:
      1) Common\Files الخاص بـ MT5 (من .env → MT5_COMMON_FILES)
      2) مجلد المشروع الرئيسي C:\EA_AI (للملفات القديمة مثل trades_202510.jsonl)
    """
    files = []

    # من Common\Files (المكان المفروض أن يكتب فيه الـ EA)
    if MT5_COMMON_FILES:
        pattern_cf = os.path.join(MT5_COMMON_FILES, f"{JSONL_FILE_PREFIX}*.jsonl")
        files_cf = glob.glob(pattern_cf)
        files.extend(files_cf)

    # من مجلد C:\EA_AI نفسه (لملفاتك القديمة الموجودة هناك)
    pattern_root = os.path.join(str(PROJECT_ROOT), f"{JSONL_FILE_PREFIX}*.jsonl")
    files_root = glob.glob(pattern_root)
    for p in files_root:
        if p not in files:
            files.append(p)

    return sorted(files)


def copy_if_newer(src: str, dst: str) -> None:
    """
    ينسخ الملف إذا:
      - الملف غير موجود في الهدف
      - أو موجود لكن حجم/تاريخ المصدر أحدث
    """
    ensure_dir(os.path.dirname(dst))

    if os.path.exists(dst):
        src_size = os.path.getsize(src)
        dst_size = os.path.getsize(dst)
        src_mtime = os.path.getmtime(src)
        dst_mtime = os.path.getmtime(dst)

        if dst_size == src_size and dst_mtime >= src_mtime:
            log(f"Skip (up-to-date): {os.path.basename(src)}")
            return

    shutil.copy2(src, dst)
    log(f"Copied: {src} -> {dst}")


def sync_jsonl() -> None:
    log(f"PROJECT_ROOT      = {PROJECT_ROOT}")
    log(f"MT5_COMMON_FILES  = {MT5_COMMON_FILES or '(not set)'}")
    log(f"JSONL_DIR (target)= {JSONL_DIR}")
    ensure_dir(JSONL_DIR)

    src_files = find_source_files()
    if not src_files:
        log("لا توجد أي ملفات trades_*.jsonl في Common\\Files أو في C:\\EA_AI.")
        return

    log(f"Found {len(src_files)} source file(s).")

    for src in src_files:
        fname = os.path.basename(src)
        dst = os.path.join(JSONL_DIR, fname)
        try:
            copy_if_newer(src, dst)
        except Exception as e:
            log(f"ERROR copying {src} -> {dst}: {e}")

    log("Sync finished.")


if __name__ == "__main__":
    sync_jsonl()
