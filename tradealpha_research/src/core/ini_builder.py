import os


class INIBuilder:
    def __init__(self, expert_name: str):
        self.expert_name = expert_name

    @staticmethod
    def _normalize_report_path(report_value: str) -> str:
        """
        Make Report a full FILE path (not a directory).
        MT5 report is most reliable when extension is provided (.htm or .html or .xml).
        Accepted inputs:
          - r"C:\...\temp"                  -> r"C:\...\temp\report\report.htm"
          - r"C:\...\temp\report"           -> r"C:\...\temp\report\report.htm"  (treat as dir/prefix)
          - r"C:\...\temp\report\report"    -> r"C:\...\temp\report\report.htm"
          - r"C:\...\temp\report\report.htm"-> same
        """
        p = os.path.abspath(report_value)

        # If it's a directory => put report\report.htm inside it
        if os.path.isdir(p):
            return os.path.join(p, "report", "report.htm")

        # If ends with slash/backslash but doesn't exist yet => treat as dir
        if p.endswith("\\") or p.endswith("/"):
            p = p.rstrip("\\/")  # remove trailing slash
            return os.path.join(p, "report", "report.htm")

        low = p.lower()
        # If user already gave a file with extension => keep it
        if low.endswith(".htm") or low.endswith(".html") or low.endswith(".xml"):
            return p

        # Otherwise treat as prefix/path and force a filename
        # If looks like "...\\report" or "...\\report\\report" => ensure we end with report.htm
        base_dir = os.path.dirname(p)
        name = os.path.basename(p)

        # If name is empty (rare) => fallback
        if not name:
            return os.path.join(base_dir, "report", "report.htm")

        # If user gave "...\\something" without extension, interpret as prefix -> add .htm
        return p + ".htm"

    def build(
        self,
        symbol: str,
        period: str,
        from_date: str,
        to_date: str,
        deposit: float,
        set_file_name: str,
        report_prefix_or_dir: str,
        ini_path: str,
        model: int = 0,
        optimization: int = 0,
        leverage: str = "1:100",
        currency: str = "USD",
        shutdown_terminal: int = 0,
        replace_report: int = 1,
    ) -> str:
        set_name_only = os.path.basename(set_file_name).strip()
        report_path = self._normalize_report_path(report_prefix_or_dir)

        # Ensure report directory exists
        os.makedirs(os.path.dirname(report_path), exist_ok=True)

        lines = [
            "[Tester]",
            f"Expert={self.expert_name}",
            f"Symbol={symbol}",
            f"Period={period}",
            f"Optimization={optimization}",
            f"Model={model}",
            f"FromDate={from_date}",
            f"ToDate={to_date}",
            f"Deposit={deposit}",
            f"Currency={currency}",
            f"Leverage={leverage}",
            f"Report={report_path}",
            f"ReplaceReport={replace_report}",
            f"ShutdownTerminal={shutdown_terminal}",
            f"ExpertParameters={set_name_only}",
        ]
        content = "\n".join(lines) + "\n"

        os.makedirs(os.path.dirname(os.path.abspath(ini_path)), exist_ok=True)
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(content)

        return ini_path