# tools/project_audit.py
# Usage:
#   python tools/project_audit.py --root C:\EA_AI --out audit_report.json
#   python tools/project_audit.py --root C:\EA_AI --out audit_report.md
# Optional:
#   python tools/project_audit.py --root C:\EA_AI --out audit_report.md --run-imports
#   python tools/project_audit.py --root C:\EA_AI --out audit_report.md --run-imports --entry tools/generate_ai_replay_csv_xgb.py
#
# What it does:
# - Scans project tree (py, json, yaml, env, toml, txt, md)
# - Extracts imports, detects missing/unused modules (best-effort)
# - Runs AST checks, syntax checks, basic static issues
# - Finds common pitfalls: duplicate/recursive column extraction, pandas key errors, circular imports
# - Detects model registry/config issues (active_model.json schema, paths exist)
# - Generates a report (JSON or Markdown) with actionable findings

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

TEXT_EXTS = {".py", ".json", ".yml", ".yaml", ".toml", ".env", ".txt", ".md", ".ini", ".cfg"}
PY_EXT = ".py"

# -----------------------------
# Utilities
# -----------------------------
def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return p.read_text(errors="replace")

def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

def is_venv_path(p: Path) -> bool:
    s = str(p).lower()
    return any(x in s for x in ["\\venv\\", "/venv/", "\\.venv\\", "/.venv/", "\\site-packages\\", "/site-packages/"])

def safe_json_load(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None

def normalize_win_path(s: str) -> str:
    # Normalize slashes, keep drive letters
    return s.replace("\\", "/")

def looks_like_abs_path(s: str) -> bool:
    s2 = normalize_win_path(s)
    return bool(re.match(r"^[A-Za-z]:/", s2)) or s2.startswith("/")

# -----------------------------
# Findings model
# -----------------------------
@dataclass
class Finding:
    severity: str  # "HIGH" | "MEDIUM" | "LOW" | "INFO"
    category: str  # e.g. "IMPORT", "SYNTAX", "CONFIG", "PANDAS", "XGBOOST"
    file: str
    line: Optional[int]
    message: str
    hint: Optional[str] = None

@dataclass
class FileInfo:
    path: str
    size: int
    mtime: float
    sha1: Optional[str] = None
    py: bool = False
    imports: Optional[List[str]] = None
    functions: Optional[List[str]] = None
    classes: Optional[List[str]] = None

# -----------------------------
# AST parsing for Python
# -----------------------------
IMPORT_RE = re.compile(r"^\s*(from|import)\s+([A-Za-z0-9_\.]+)", re.M)

def parse_py_ast(code: str, file_path: str) -> Tuple[Optional[ast.AST], List[Finding]]:
    findings: List[Finding] = []
    try:
        tree = ast.parse(code, filename=file_path)
        return tree, findings
    except SyntaxError as e:
        findings.append(Finding(
            severity="HIGH",
            category="SYNTAX",
            file=file_path,
            line=e.lineno,
            message=f"SyntaxError: {e.msg}",
            hint="Fix syntax to allow further analysis."
        ))
        return None, findings
    except Exception as e:
        findings.append(Finding(
            severity="HIGH",
            category="SYNTAX",
            file=file_path,
            line=None,
            message=f"AST parse failed: {e}",
            hint="Open the file and check for encoding or severe corruption."
        ))
        return None, findings

def extract_imports_from_ast(tree: ast.AST) -> List[str]:
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    return sorted(set(out))

def extract_defs_from_ast(tree: ast.AST) -> Tuple[List[str], List[str]]:
    funcs: List[str] = []
    classes: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.append(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    return sorted(set(funcs)), sorted(set(classes))

# -----------------------------
# Heuristic detectors (project-specific)
# -----------------------------
def detect_registry_schema(root: Path, findings: List[Finding]) -> None:
    # Tries to locate typical model registry files
    candidates = [
        root / "models" / "active_model.json",
        root / "app" / "models" / "active_model.json",
        root / "active_model.json",
    ]
    for p in candidates:
        if p.exists():
            txt = read_text(p)
            j = safe_json_load(txt)
            if j is None:
                findings.append(Finding("HIGH", "CONFIG", rel(p, root), None,
                                        "active_model.json is not valid JSON",
                                        "Rewrite it as valid JSON. Expected keys: {'active': 'path/to/model.bin'}"))
                continue
            if "active" not in j:
                findings.append(Finding("HIGH", "CONFIG", rel(p, root), None,
                                        "active_model.json missing key 'active'",
                                        "Set: {\"active\": \"C:/EA_AI/models/<model>.bin\"}"))
            else:
                ap = str(j.get("active") or "")
                if not ap:
                    findings.append(Finding("HIGH", "CONFIG", rel(p, root), None,
                                            "active_model.json has empty 'active' path",
                                            "Point 'active' to an existing model .bin/.json/.pkl file."))
                else:
                    # check existence
                    ap_norm = normalize_win_path(ap)
                    model_path = Path(ap_norm) if looks_like_abs_path(ap_norm) else (p.parent / ap_norm)
                    if not model_path.exists():
                        findings.append(Finding("HIGH", "CONFIG", rel(p, root), None,
                                                f"Active model path does not exist: {ap}",
                                                "Fix the path or copy the model to that location."))

def detect_xgb_pickle_risk(root: Path, findings: List[Finding]) -> None:
    # Look for joblib.load on xgboost models saved by older versions; warn to use Booster.save_model
    for py in root.rglob("*.py"):
        if is_venv_path(py):
            continue
        code = read_text(py)
        if "joblib.load" in code and "xgboost" in code:
            findings.append(Finding(
                severity="MEDIUM",
                category="XGBOOST",
                file=rel(py, root),
                line=None,
                message="Potential XGBoost serialization compatibility issue (joblib/pickle load).",
                hint="Prefer saving/loading via Booster.save_model / Booster.load_model (JSON/UBJ) for long-term stability."
            ))

def detect_pandas_column_pitfalls(root: Path, findings: List[Finding]) -> None:
    # Catch suspicious recursion patterns similar to your earlier recursion error in _extract_field / _as_series
    patterns = [
        (re.compile(r"def\s+_as_series\s*\(", re.M), "Custom _as_series helper found; ensure it cannot recurse on duplicate columns."),
        (re.compile(r"def\s+_extract_field\s*\(", re.M), "Custom _extract_field found; ensure correct handling for duplicate column names and MultiIndex."),
        (re.compile(r"RecursionError", re.M), "RecursionError mention in code/comments; investigate recursion guards."),
        (re.compile(r"drop_duplicates\(keep=False\)", re.M), "Using drop_duplicates(keep=False) can be expensive on huge columns; ensure it's necessary."),
    ]
    for py in root.rglob("*.py"):
        if is_venv_path(py):
            continue
        code = read_text(py)
        for pat, msg in patterns:
            if pat.search(code):
                findings.append(Finding(
                    severity="INFO",
                    category="PANDAS",
                    file=rel(py, root),
                    line=None,
                    message=msg,
                    hint="Add explicit guards for DataFrame-vs-Series and duplicate column names."
                ))

def detect_hardcoded_paths(root: Path, findings: List[Finding]) -> None:
    # Flags absolute Windows paths in source (common cause of portability issues)
    abs_pat = re.compile(r"[A-Za-z]:\\\\|[A-Za-z]:/|\\\\Users\\\\|/Users/")
    for p in root.rglob("*"):
        if p.is_dir() or is_venv_path(p) or p.suffix.lower() not in TEXT_EXTS:
            continue
        txt = read_text(p)
        if abs_pat.search(txt):
            findings.append(Finding(
                severity="LOW",
                category="PORTABILITY",
                file=rel(p, root),
                line=None,
                message="Hard-coded absolute path detected.",
                hint="Move paths to config/env (settings) and build them via Path()."
            ))

def detect_circular_import_risk(py_files: List[Path], root: Path, import_map: Dict[str, Set[str]], findings: List[Finding]) -> None:
    # Build a simple module graph from local imports (best-effort)
    # Map file -> module name based on relative path
    def mod_name(p: Path) -> str:
        rp = rel(p, root).replace("\\", "/")
        if rp.endswith(".py"):
            rp = rp[:-3]
        rp = rp.replace("/", ".")
        if rp.endswith(".__init__"):
            rp = rp[: -len(".__init__")]
        return rp

    local_mods = {mod_name(p): p for p in py_files}
    graph: Dict[str, Set[str]] = {m: set() for m in local_mods.keys()}

    for m, imports in import_map.items():
        if m not in graph:
            continue
        for imp in imports:
            # keep only imports that point to local modules (prefix match)
            for lm in local_mods.keys():
                if imp == lm or imp.startswith(lm + "."):
                    graph[m].add(lm)
                    break

    # detect cycles via DFS
    visited: Set[str] = set()
    stack: Set[str] = set()

    def dfs(u: str, path: List[str]):
        visited.add(u)
        stack.add(u)
        for v in graph.get(u, set()):
            if v not in visited:
                dfs(v, path + [v])
            elif v in stack:
                cyc = " -> ".join(path + [v])
                findings.append(Finding(
                    severity="MEDIUM",
                    category="IMPORT",
                    file=str(local_mods.get(u, "")),
                    line=None,
                    message=f"Possible circular import cycle: {cyc}",
                    hint="Break cycles by moving shared code to a separate module or using local imports inside functions."
                ))
        stack.remove(u)

    for m in graph.keys():
        if m not in visited:
            dfs(m, [m])

# -----------------------------
# Import test runner (optional)
# -----------------------------
def run_import_checks(root: Path, entry: Optional[str], findings: List[Finding]) -> None:
    # Tries to import top-level packages in the project (best effort)
    # Adds root to sys.path temporarily.
    sys.path.insert(0, str(root))
    try:
        # Try importing "app" if exists
        if (root / "app").exists():
            try:
                __import__("app")
            except Exception as e:
                findings.append(Finding(
                    severity="HIGH",
                    category="IMPORT",
                    file="(runtime)",
                    line=None,
                    message=f"Import 'app' failed: {e}",
                    hint="Set PYTHONPATH to project root or ensure app/ is a package with __init__.py."
                ))
        # If entry script specified, attempt runpy on it (without executing main by default)
        if entry:
            import runpy
            ep = (root / entry).resolve()
            if ep.exists():
                try:
                    runpy.run_path(str(ep), run_name="__audit__")
                except SystemExit:
                    # argparse may call sys.exit; ignore
                    pass
                except Exception as e:
                    findings.append(Finding(
                        severity="MEDIUM",
                        category="RUNTIME",
                        file=entry,
                        line=None,
                        message=f"Running entry script raised: {e}",
                        hint="Check traceback for missing deps, bad globals, or argparse assumptions."
                    ))
    finally:
        try:
            sys.path.remove(str(root))
        except ValueError:
            pass

# -----------------------------
# Main scan
# -----------------------------
def scan_project(root: Path, run_imports: bool, entry: Optional[str]) -> dict:
    findings: List[Finding] = []
    files: List[FileInfo] = []

    py_files: List[Path] = []

    # gather files
    for p in root.rglob("*"):
        if p.is_dir() or is_venv_path(p):
            continue
        if p.suffix.lower() not in TEXT_EXTS:
            continue

        st = p.stat()
        fi = FileInfo(
            path=rel(p, root),
            size=int(st.st_size),
            mtime=float(st.st_mtime),
            py=(p.suffix.lower() == ".py")
        )

        if fi.py:
            py_files.append(p)
            code = read_text(p)
            tree, syn_findings = parse_py_ast(code, fi.path)
            findings.extend(syn_findings)
            if tree is not None:
                imps = extract_imports_from_ast(tree)
                funcs, clss = extract_defs_from_ast(tree)
                fi.imports = imps
                fi.functions = funcs
                fi.classes = clss
            else:
                # fallback regex import extraction
                imps = [m.group(2) for m in IMPORT_RE.finditer(code)]
                fi.imports = sorted(set(imps))

        files.append(fi)

    # build import map (module -> imports)
    # module name heuristic from path
    def mod_name(p: Path) -> str:
        rp = rel(p, root).replace("\\", "/")
        rp = rp[:-3]  # strip .py
        rp = rp.replace("/", ".")
        if rp.endswith(".__init__"):
            rp = rp[: -len(".__init__")]
        return rp

    import_map: Dict[str, Set[str]] = {}
    for p in py_files:
        code = read_text(p)
        tree, _ = parse_py_ast(code, rel(p, root))
        if tree is None:
            imps = set([m.group(2) for m in IMPORT_RE.finditer(code)])
        else:
            imps = set(extract_imports_from_ast(tree))
        import_map[mod_name(p)] = imps

    # heuristic detectors
    detect_registry_schema(root, findings)
    detect_xgb_pickle_risk(root, findings)
    detect_pandas_column_pitfalls(root, findings)
    detect_hardcoded_paths(root, findings)
    detect_circular_import_risk(py_files, root, import_map, findings)

    # optional runtime import checks
    if run_imports:
        run_import_checks(root, entry, findings)

    # summarize
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    findings_sorted = sorted(findings, key=lambda f: (sev_order.get(f.severity, 9), f.category, f.file, f.line or 0))

    summary = {
        "HIGH": sum(1 for f in findings_sorted if f.severity == "HIGH"),
        "MEDIUM": sum(1 for f in findings_sorted if f.severity == "MEDIUM"),
        "LOW": sum(1 for f in findings_sorted if f.severity == "LOW"),
        "INFO": sum(1 for f in findings_sorted if f.severity == "INFO"),
        "total_files": len(files),
        "python_files": sum(1 for x in files if x.py),
        "generated_at": now_iso(),
        "root": str(root),
    }

    return {
        "summary": summary,
        "findings": [asdict(f) for f in findings_sorted],
        "files": [asdict(x) for x in files],
    }

def write_report(report: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".json":
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    # Markdown
    s = report["summary"]
    lines: List[str] = []
    lines.append(f"# Project Audit Report\n")
    lines.append(f"- Root: `{s['root']}`")
    lines.append(f"- Generated: `{s['generated_at']}`")
    lines.append(f"- Files: **{s['total_files']}** (Python: **{s['python_files']}**)")
    lines.append(f"- Findings: HIGH={s['HIGH']}, MEDIUM={s['MEDIUM']}, LOW={s['LOW']}, INFO={s['INFO']}\n")

    # Findings by severity
    def sec(title: str):
        lines.append(f"## {title}\n")

    by_sev: Dict[str, List[dict]] = {"HIGH": [], "MEDIUM": [], "LOW": [], "INFO": []}
    for f in report["findings"]:
        by_sev.get(f["severity"], []).append(f)

    for sev in ["HIGH", "MEDIUM", "LOW", "INFO"]:
        sec(f"{sev} Findings ({len(by_sev[sev])})")
        if not by_sev[sev]:
            lines.append("_None_\n")
            continue
        for f in by_sev[sev]:
            loc = f"{f['file']}"
            if f.get("line"):
                loc += f":{f['line']}"
            lines.append(f"- **[{f['category']}]** `{loc}` — {f['message']}")
            if f.get("hint"):
                lines.append(f"  - Hint: {f['hint']}")
        lines.append("")

    # File index (small)
    sec("Files Index (Python)")
    py_files = [x for x in report["files"] if x.get("py")]
    for x in sorted(py_files, key=lambda z: z["path"])[:200]:
        lines.append(f"- `{x['path']}`  (functions={len(x.get('functions') or [])}, classes={len(x.get('classes') or [])})")
    if len(py_files) > 200:
        lines.append(f"\n_... truncated, total python files: {len(py_files)}_\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Project root folder (e.g. C:\\EA_AI)")
    ap.add_argument("--out", required=True, help="Output report path (.json or .md)")
    ap.add_argument("--run-imports", action="store_true", help="Attempt runtime imports (may require env/deps)")
    ap.add_argument("--entry", default=None, help="Optional entry script to run_path for smoke-test (relative to root)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"ERROR: root not found: {root}")
        sys.exit(2)

    report = scan_project(root, run_imports=args.run_imports, entry=args.entry)
    write_report(report, Path(args.out).resolve())

    s = report["summary"]
    print(f"OK: wrote report -> {Path(args.out).resolve()}")
    print(f"Summary: HIGH={s['HIGH']} MEDIUM={s['MEDIUM']} LOW={s['LOW']} INFO={s['INFO']} files={s['total_files']} py={s['python_files']}")

if __name__ == "__main__":
    main()
