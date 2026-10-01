"""Weekly sales reports."""


def weekly_summary(rows):
    """Total the amount per rep. ``rows`` is a list of {"rep": str, "amount": number}."""
    totals = {}
    for row in rows:
        totals[row["rep"]] = totals.get(row["rep"], 0) + row["amount"]
    return [{"rep": rep, "total": total} for rep, total in sorted(totals.items())]
