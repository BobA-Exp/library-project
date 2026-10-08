import pandas as pd

from processing.analytics import DataProcessor

WORKS = [
    {
        "work_key": "/works/OL1W",
        "title": "The Hobbit",
        "first_publish_year": "1937-09-21",
        "languages": ["eng"],
        "author_keys": ["/authors/OL1A"],
        "author_names": ["Tolkien"],
    },
    {
        "work_key": "/works/OL2W",
        "title": None,
        "first_publish_year": None,
        "languages": [],
        "author_keys": ["/authors/OL1A"],
        "author_names": ["Tolkien"],
    },
    {
        "work_key": "/works/OL3W",
        "title": "Dune",
        "first_publish_year": "1965",
        "languages": ["eng", "fre"],
        "author_keys": ["/authors/OL2A"],
        "author_names": ["Herbert"],
    },
    {
        "work_key": "/works/OL4W",
        "title": "Dated",
        "first_publish_year": "September 21, 1937",
        "languages": ["spa"],
        "author_keys": ["/authors/OL2A"],
        "author_names": ["Herbert"],
    },
]

AUTHORS = [
    {"author_key": "/authors/OL1A", "name": "Tolkien"},
    {"author_key": "/authors/OL2A", "name": "Herbert"},
]


def test_catalog_frame_fills_missing_values_and_parses_dates() -> None:
    frame = DataProcessor(WORKS, AUTHORS).catalog_frame()

    assert frame.loc[frame["work_key"] == "/works/OL2W", "title"].item() == "Unknown"
    years = dict(zip(frame["work_key"], frame["publication_year"], strict=True))
    assert int(years["/works/OL1W"]) == 1937
    assert int(years["/works/OL3W"]) == 1965
    assert int(years["/works/OL4W"]) == 1937
    assert pd.isna(years["/works/OL2W"])


def test_author_join_and_aggregations() -> None:
    processor = DataProcessor(WORKS, AUTHORS)
    joined = processor.author_works()
    years = processor.top_publication_years()
    languages = processor.top_languages()
    metrics = processor.performance_metrics().set_index("metric")["value"]

    hobbit = joined.loc[joined["work_key"] == "/works/OL1W"].iloc[0]
    assert hobbit["name"] == "Tolkien"
    assert years["publication_year"].tolist() == [1937, 1965]
    assert years["work_count"].tolist() == [2, 1]
    assert languages["language"].tolist() == ["eng", "fre", "spa", "unknown"]
    assert int(metrics["work_count"]) == 4
    assert int(metrics["missing_year_count"]) == 1
    assert int(metrics["known_language_count"]) == 3
    assert int(metrics["earliest_year"]) == 1937
    assert int(metrics["latest_year"]) == 1965
