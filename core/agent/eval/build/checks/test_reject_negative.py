import pytest

from reports import weekly_summary


def test_a_negative_amount_is_rejected():
    with pytest.raises(ValueError):
        weekly_summary([{"rep": "a", "amount": -1}])


def test_valid_rows_still_total():
    assert weekly_summary([{"rep": "a", "amount": 1}, {"rep": "a", "amount": 2}]) == [{"rep": "a", "total": 3}]
