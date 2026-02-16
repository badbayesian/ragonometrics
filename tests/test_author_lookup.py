"""Author lookup tests for metadata fallback from page text."""

from ragonometrics.core.main import (
    _format_author_names,
    _select_best_author_names,
    extract_author_names,
    infer_author_names_from_pages,
)


def test_infer_author_names_cumulative_sums():
    page = """
    Use of Cumulative Sums of Squares for Retrospective Detection of Changes of
    Variance
             Carla Inclan; George C. Tiao
             Journal of the American Statistical Association, Vol. 89, No. 427.
    """
    names = infer_author_names_from_pages([page])
    assert names == ["Carla Inclan", "George C. Tiao"]


def test_infer_author_names_incomplete_disclosure():
    page = """
    Incomplete Disclosure: Evidence of Signaling and
    Countersignaling†
    By Benjamin B. Bederson, Ginger Zhe Jin, Phillip Leslie,
    Alexander J. Quinn, and Ben Zou*
    """
    names = infer_author_names_from_pages([page])
    assert names == [
        "Benjamin B. Bederson",
        "Ginger Zhe Jin",
        "Phillip Leslie",
        "Alexander J. Quinn",
        "Ben Zou",
    ]


def test_infer_author_names_managerial_incentives():
    page = """
    Managerial Incentives and Strategic Change: Evidence
    from Private Equity∗
    Phillip Leslie and Paul Oyer†
    """
    names = infer_author_names_from_pages([page])
    assert names == ["Phillip Leslie", "Paul Oyer"]


def test_select_best_author_names_prefers_richer_source():
    pdfinfo_names = extract_author_names("Benjamin B. Bederson")
    page_text_names = [
        "Benjamin B. Bederson",
        "Ginger Zhe Jin",
        "Phillip Leslie",
        "Alexander J. Quinn",
        "Ben Zou",
    ]
    selected = _select_best_author_names(
        [
            ("openalex", []),
            ("page_text", page_text_names),
            ("pdfinfo", pdfinfo_names),
        ]
    )
    assert selected == page_text_names
    assert _format_author_names(selected) == (
        "Benjamin B. Bederson, Ginger Zhe Jin, Phillip Leslie, "
        "Alexander J. Quinn, Ben Zou"
    )
