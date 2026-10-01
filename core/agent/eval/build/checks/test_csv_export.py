from reports import to_csv


def test_to_csv_has_a_header_and_one_line_per_rep():
    out = to_csv([{"rep": "a", "total": 2}, {"rep": "b", "total": 6}])
    assert out.strip().splitlines() == ["rep,total", "a,2", "b,6"]


def test_to_csv_quotes_a_comma_in_a_name():
    out = to_csv([{"rep": "Lee, Sam", "total": 1}])
    assert out.strip().splitlines()[1] == '"Lee, Sam",1'
