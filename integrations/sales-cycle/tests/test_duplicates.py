"""Recognising the same feature request worded differently — and keeping different requests apart."""
from sales_cycle.duplicates import Known, find_duplicate, similarity, words


def _dup(title, body, *known):
    return find_duplicate(title, body, [Known(i, t, b) for i, (t, b) in enumerate(known, 1)])


def test_the_real_case_the_same_csv_ask_worded_two_ways():
    got = _dup("Export results to CSV", "I suggest adding a button to export the results to CSV.",
               ("CSV Export of Results", "We should add a button to change the results to CSV for export."))
    assert got is not None and got.id == 1


def test_an_identical_request_is_a_duplicate():
    assert _dup("Dark mode", "Add a dark mode for the dashboard.", ("Dark mode", "Add a dark mode for the dashboard.")).id == 1


def test_case_plurals_and_filler_do_not_matter():
    assert _dup("BULK EDIT ROWS", "We need the ability to bulk edit rows.", ("Bulk editing row", "Customers want to bulk edit rows please")) is not None


def test_a_different_format_is_not_the_same_request():
    assert _dup("Export results to PDF", "Add a button to export results to PDF.", ("Export results to CSV", "Add a button to export results to CSV.")) is None


def test_different_features_sharing_a_generic_word_stay_apart():
    assert _dup("Export results to CSV", "Button for export.", ("Export billing to Salesforce", "Sync billing export into Salesforce.")) is None
    assert _dup("Slack notifications", "Notify me in Slack.", ("Email notifications", "Notify me by email.")) is None


def test_a_single_shared_word_is_never_enough():
    assert _dup("Export", "Export.", ("Export", "Export.")) is None
    assert similarity("Export", "", "Export", "") == 0.0


def test_a_very_different_request_is_not_a_duplicate():
    assert _dup("UI colour palette", "Make the colours purple.", ("CSV Export of Results", "Add a CSV button.")) is None


def test_the_earliest_original_wins_when_several_match():
    got = _dup("Export results to CSV", "Button to export results to CSV.",
               ("CSV export of results", "Export button for results as CSV."), ("Results CSV export", "Export results CSV."))
    assert got.id == 1


def test_nothing_known_means_no_duplicate():
    assert _dup("Anything", "at all") is None


def test_words_reduces_a_sentence_to_what_is_asked_for():
    assert words("We should add a button to export the results to CSV") == {"button", "export", "result", "csv"}
