"""Author lookup tests for metadata fallback from page text."""

from pathlib import Path

from ragonometrics.core.main import (
    _apply_paper_metadata_overrides,
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


def test_apply_paper_metadata_overrides_incomplex_alternatives_to_mixed_bundling():
    path = Path("papers/Incomplex Alternatives to Mixed Bundling - Chu et al. (2006).pdf")
    title = "Incomplex Alternatives to Mixed Bundling - Chu et al. (2006)"
    author = "Lanier Benkard, Garth Saloner, Andy Skrzypacz"
    patched_title, patched_author = _apply_paper_metadata_overrides(path, title, author)
    assert patched_title == title
    assert patched_author == "Chenghuan Sean Chu, Phillip Leslie, Alan Sorensen"


def test_apply_paper_metadata_overrides_calorie_posting_in_chain_restaurants():
    path = Path("papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf")
    title = "Calorie Posting in Chain Restaurants"
    author = "Unknown"
    patched_title, patched_author = _apply_paper_metadata_overrides(path, title, author)
    assert patched_title == title
    assert patched_author == "Bryan Bollinger, Phillip Leslie, and Alan Sorensen"


def test_apply_paper_metadata_overrides_leslie_related_titles():
    cases = [
        (
            "papers/Bundle-Size Pricing as an Approximation to Mixed Bundling - Chu et al. (2010).pdf",
            "Bundle-Size Pricing as an Approximation to Mixed Bundling",
            "Unknown",
            "Chenghuan Sean Chu, Phillip Leslie, and Alan Sorensen",
        ),
        (
            "papers/Information Entrepreneurs and Competition in Procurement Auctions - Leslie et al. (2021).pdf",
            "Information Entrepreneurs and Competition in Procurement Auctions",
            "Unknown",
            "Phillip Leslie and Pablo Zoido",
        ),
        (
            "papers/Managerial Incentives and Strategic Change Evidence from Private Equity - Leslie et al. (2008).pdf",
            "Managerial Incentives and Strategic Change Evidence from Private Equity",
            "Unknown",
            "Phillip Leslie and Paul Oyer",
        ),
        (
            "papers/Nearly Optimal Pricing for Multiproduct Firms - Chu et al. (2008).pdf",
            "Nearly Optimal Pricing for Multiproduct Firms",
            "Unknown",
            "Chenghuan Sean Chu, Phillip Leslie, and Alan Sorensen",
        ),
        (
            "papers/The Welfare Effects of Ticket Resale - Leslie et al. (2009).pdf",
            "The Welfare Effects of Ticket Resale",
            "Unknown",
            "Phillip Leslie and Alan Sorensen",
        ),
        (
            "papers/Ticket Resale - Leslie et al. (2007).pdf",
            "Ticket Resale",
            "Unknown",
            "Phillip Leslie and Alan Sorensen",
        ),
    ]

    for path_text, title, author, expected in cases:
        patched_title, patched_author = _apply_paper_metadata_overrides(Path(path_text), title, author)
        assert patched_title == title
        assert patched_author == expected
