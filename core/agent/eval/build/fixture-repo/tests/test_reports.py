from reports import weekly_summary


def test_weekly_summary_totals_per_rep():
    rows = [{"rep": "b", "amount": 5}, {"rep": "a", "amount": 2}, {"rep": "b", "amount": 1}]
    assert weekly_summary(rows) == [{"rep": "a", "total": 2}, {"rep": "b", "total": 6}]
