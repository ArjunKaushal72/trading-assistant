"""Daily point-in-time snapshot job (installed at Step 4, after the FMP probe).

Runs once daily after both TSX and NYSE close. Appends date-stamped rows to
append-only DuckDB tables so that fundamentals, ETF holdings, and congressional
disclosures become honestly backtestable later. Every day skipped is a day of
future research permanently lost, which is why this starts at kickoff even
though nothing consumes it yet.

Placeholder: wired up in Step 4 once the probe fixes the data branch.
"""

def main() -> int:
    raise NotImplementedError("Built in Step 4 after the FMP probe result.")


if __name__ == "__main__":
    raise SystemExit(main())
