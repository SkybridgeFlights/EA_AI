import os
import glob
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple


def _read_tail_any_encoding(path: str, max_bytes: int = 250_000) -> str:
    """Read the last part of a file and decode using common MT5 encodings."""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            blob = f.read()
    except OSError:
        return ""

    # MT5 logs are often UTF-16LE
    for enc in ("utf-16le", "utf-8", "cp1252"):
        try:
            return blob.decode(enc, errors="ignore")
        except Exception:
            continue
    return blob.decode("utf-8", errors="ignore")


def _parse_ini_value(ini_path: Optional[str], section: str, key: str) -> Optional[str]:
    """Minimal INI parser for MT5 /config files."""
    if not ini_path or not os.path.exists(ini_path):
        return None

    cur: Optional[str] = None
    try:
        with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                m = re.match(r"^\[(.+)\]$", line)
                if m:
                    cur = m.group(1).strip()
                    continue
                if cur == section and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return v.strip()
    except OSError:
        return None

    return None


@dataclass
class WaitResult:
    report_path: Optional[str]
    log_path: Optional[str]
    finished: bool
    finish_reason: Optional[str]
    final_balance: Optional[float]
    test_passed: Optional[bool]
    summary_path: Optional[str]


class MT5Executor:
    """Run MT5 Strategy Tester via /config and wait for completion.

    Fixes:
    - Decode MT5 logs correctly (often UTF-16LE).
    - Detect finish reliably from full tail (not incremental bytes).
    - Prefer Report=... from INI when provided.
    - If MT5 writes report elsewhere, search common report folders and copy it.
    """

    FINISH_MARKERS = (
        "automatical testing finished",
        "test passed in",
        "test failed",
        "testing finished",
    )

    RE_FINAL_BALANCE = re.compile(r"final balance\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
    RE_TEST_PASSED = re.compile(r"\bTest passed\b", re.IGNORECASE)
    RE_TEST_FAILED = re.compile(r"\bTest failed\b", re.IGNORECASE)

    REPORT_EXTS = (".htm", ".html", ".xml")

    def __init__(self, mt5_path: str):
        self.mt5_path = mt5_path
        self._proc: Optional[subprocess.Popen] = None

    def run(self, ini_path: str) -> int:
        ini_abs = os.path.abspath(ini_path)
        cmd = [self.mt5_path, f"/config:{ini_abs}"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    def wait_for_report_professional(
        self,
        report_prefix: str,
        ini_path: Optional[str] = None,
        timeout_sec: int = 3600,
        poll_sec: float = 1.0,
        post_finish_grace_sec: float = 3.0,
        summary_json_path: Optional[str] = None,
    ) -> WaitResult:
        start_ts = time.time()

        # Prefer Report=... from INI if available (it can be a full file path).
        rp = os.path.abspath(report_prefix)
        ini_report = _parse_ini_value(ini_path, "Tester", "Report")
        if ini_report:
            rp = os.path.abspath(ini_report)

        candidates = self._report_candidates(rp)

        log_path: Optional[str] = None
        saw_finish = False
        finish_reason: Optional[str] = None
        last_log_mtime = 0.0

        final_balance: Optional[float] = None
        test_passed: Optional[bool] = None

        while time.time() - start_ts < timeout_sec:
            newest_log, newest_mtime = self._find_newest_tester_log()
            if newest_log and (log_path is None or newest_mtime > last_log_mtime):
                log_path = newest_log
                last_log_mtime = newest_mtime

            if log_path and os.path.exists(log_path):
                tail_text = _read_tail_any_encoding(log_path)
                lines = [
                    ln
                    for ln in tail_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    if ln.strip()
                ]

                fb, tp = self._parse_metrics_from_lines(lines)
                if fb is not None:
                    final_balance = fb
                if tp is not None:
                    test_passed = tp

                marker = self._detect_finish_marker(tail_text)
                if marker:
                    saw_finish = True
                    finish_reason = marker

                    # Give MT5 a moment to flush report files.
                    time.sleep(post_finish_grace_sec)

                    # 1) Try configured target first.
                    report_found = self._first_existing_report(candidates)

                    # 2) If missing, search common MT5 report locations and copy into our target.
                    if report_found is None:
                        found_any = self._find_newest_report_anywhere(since_ts=start_ts)
                        if found_any:
                            report_found = self._copy_report_to_target(found_any, rp)

                    sp = self._write_summary_json(
                        summary_json_path=summary_json_path,
                        report_prefix=rp,
                        report_path=report_found,
                        log_path=log_path,
                        finished=True,
                        finish_reason=finish_reason,
                        final_balance=final_balance,
                        test_passed=test_passed,
                    )

                    return WaitResult(
                        report_path=report_found,
                        log_path=log_path,
                        finished=True,
                        finish_reason=finish_reason,
                        final_balance=final_balance,
                        test_passed=test_passed,
                        summary_path=sp,
                    )

            time.sleep(poll_sec)

        # Timeout best effort.
        report_found = self._first_existing_report(candidates)
        if report_found is None:
            found_any = self._find_newest_report_anywhere(since_ts=start_ts)
            if found_any:
                report_found = self._copy_report_to_target(found_any, rp)

        sp = self._write_summary_json(
            summary_json_path=summary_json_path,
            report_prefix=rp,
            report_path=report_found,
            log_path=log_path,
            finished=saw_finish,
            finish_reason=finish_reason or "timeout",
            final_balance=final_balance,
            test_passed=test_passed,
        )

        return WaitResult(
            report_path=report_found,
            log_path=log_path,
            finished=saw_finish,
            finish_reason=finish_reason or "timeout",
            final_balance=final_balance,
            test_passed=test_passed,
            summary_path=sp,
        )

    # -----------------------
    # Report helpers
    # -----------------------

    def _report_candidates(self, rp_abs: str) -> List[str]:
        ext = os.path.splitext(rp_abs)[1].lower()
        if ext:
            return [rp_abs]
        return [rp_abs + ".htm", rp_abs + ".html", rp_abs + ".xml"]

    def _first_existing_report(self, candidates: List[str]) -> Optional[str]:
        best = None
        best_mtime = 0.0
        for c in candidates:
            try:
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    mt = os.path.getmtime(c)
                    if mt >= best_mtime:
                        best_mtime = mt
                        best = c
            except OSError:
                continue
        return best

    def _find_newest_report_anywhere(self, since_ts: float) -> Optional[str]:
        """Search common MT5 report folders for the newest report after since_ts."""
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")

        roots: List[str] = []
        if appdata:
            roots.append(os.path.join(appdata, "MetaQuotes"))
        if localappdata:
            roots.append(os.path.join(localappdata, "MetaQuotes"))

        patterns: List[str] = []
        for r in roots:
            patterns += [
                os.path.join(r, "Terminal", "*", "Tester", "reports", "*"),
                os.path.join(r, "Terminal", "*", "Tester", "Reports", "*"),
                os.path.join(r, "Tester", "*", "reports", "*"),
                os.path.join(r, "Tester", "*", "Reports", "*"),
                # broader scans
                os.path.join(r, "Terminal", "*", "**", "*.htm"),
                os.path.join(r, "Terminal", "*", "**", "*.html"),
                os.path.join(r, "Terminal", "*", "**", "*.xml"),
                os.path.join(r, "Tester", "*", "**", "*.htm"),
                os.path.join(r, "Tester", "*", "**", "*.html"),
                os.path.join(r, "Tester", "*", "**", "*.xml"),
            ]

        newest_path = None
        newest_mtime = 0.0

        for pat in patterns:
            for p in glob.glob(pat, recursive=True):
                try:
                    if not os.path.isfile(p):
                        continue
                    ext = os.path.splitext(p)[1].lower()
                    if ext not in self.REPORT_EXTS:
                        continue
                    mt = os.path.getmtime(p)
                    if mt < since_ts:
                        continue
                    if os.path.getsize(p) <= 0:
                        continue
                    if mt > newest_mtime:
                        newest_mtime = mt
                        newest_path = p
                except OSError:
                    continue

        return newest_path

    def _copy_report_to_target(self, src_report: str, target_prefix_or_path: str) -> Optional[str]:
        """Copy src_report into target path (file or prefix)."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(target_prefix_or_path)), exist_ok=True)
        except OSError:
            pass

        target_ext = os.path.splitext(target_prefix_or_path)[1].lower()
        src_ext = os.path.splitext(src_report)[1].lower()

        if target_ext in self.REPORT_EXTS:
            dst = target_prefix_or_path
        else:
            dst = target_prefix_or_path + (src_ext if src_ext in self.REPORT_EXTS else ".htm")

        try:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            shutil.copy2(src_report, dst)
            return dst
        except OSError:
            return None

    # -----------------------
    # Log helpers
    # -----------------------

    def _detect_finish_marker(self, tail_text: str) -> Optional[str]:
        low = (tail_text or "").lower()
        for m in self.FINISH_MARKERS:
            if m in low:
                return m
        # Fallback: sometimes we only see final balance + pass/fail
        if "final balance" in low and ("test passed" in low or "test failed" in low):
            return "final balance + test result"
        return None

    def _parse_metrics_from_lines(self, lines: List[str]) -> Tuple[Optional[float], Optional[bool]]:
        fb: Optional[float] = None
        tp: Optional[bool] = None
        for ln in lines:
            m = self.RE_FINAL_BALANCE.search(ln)
            if m:
                try:
                    fb = float(m.group(1))
                except ValueError:
                    pass
            if self.RE_TEST_PASSED.search(ln):
                tp = True
            if self.RE_TEST_FAILED.search(ln):
                tp = False
        return fb, tp

    def _find_newest_tester_log(self) -> Tuple[Optional[str], float]:
        """Find newest *.log under common MT5 tester log locations."""
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")

        candidates_roots: List[str] = []
        if appdata:
            candidates_roots.append(os.path.join(appdata, "MetaQuotes"))
        if localappdata:
            candidates_roots.append(os.path.join(localappdata, "MetaQuotes"))

        patterns: List[str] = []
        for r in candidates_roots:
            patterns += [
                os.path.join(r, "Terminal", "*", "Tester", "logs", "*.log"),
                os.path.join(r, "Terminal", "*", "Tester", "Agent-*", "logs", "*.log"),
                os.path.join(r, "Tester", "*", "Agent-*", "logs", "*.log"),
                os.path.join(r, "Tester", "*", "logs", "*.log"),
            ]

        newest_path = None
        newest_mtime = 0.0

        for pat in patterns:
            for p in glob.glob(pat):
                try:
                    mt = os.path.getmtime(p)
                    if mt > newest_mtime:
                        newest_mtime = mt
                        newest_path = p
                except OSError:
                    continue

        return newest_path, newest_mtime

    # -----------------------
    # Summary writer
    # -----------------------

    def _write_summary_json(
        self,
        summary_json_path: Optional[str],
        report_prefix: str,
        report_path: Optional[str],
        log_path: Optional[str],
        finished: bool,
        finish_reason: Optional[str],
        final_balance: Optional[float],
        test_passed: Optional[bool],
    ) -> Optional[str]:
        base_dir = os.path.dirname(report_prefix)
        sp = summary_json_path or os.path.join(base_dir, "result_summary.json")

        payload = {
            "finished": finished,
            "finish_reason": finish_reason,
            "log_path": log_path,
            "report_prefix": report_prefix,
            "report_path": report_path,
            "final_balance": final_balance,
            "test_passed": test_passed,
            "ts": time.time(),
        }

        try:
            os.makedirs(os.path.dirname(os.path.abspath(sp)), exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return sp
        except OSError:
            return None