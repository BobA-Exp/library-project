"""Pandas transformations for the local catalog."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

_YEAR = re.compile(r"\b(1\d{3}|20\d{2})\b")
_WORK_COLUMNS = (
    "work_key",
    "title",
    "first_publish_year",
    "languages",
    "author_keys",
    "author_names",
)


class DataProcessor:
    """Clean catalog rows and aggregate them with pandas."""

    def __init__(
        self,
        works: Sequence[Mapping[str, Any]] | None = None,
        authors: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self._works = pd.DataFrame(list(works or []))
        self._authors = pd.DataFrame(list(authors or []))

    def catalog_frame(self) -> pd.DataFrame:
        """Return works with missing values filled and publication years parsed."""
        frame = self._works.copy()
        for column in _WORK_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        frame["title"] = frame["title"].map(_fill_title)
        frame["languages"] = frame["languages"].map(_string_items)
        frame["author_keys"] = frame["author_keys"].map(_string_items)
        frame["author_names"] = frame["author_names"].map(_string_items)
        frame["publication_year"] = frame["first_publish_year"].map(_publication_year)
        frame["publication_year"] = frame["publication_year"].astype("Int64")
        return frame

    def author_works(self) -> pd.DataFrame:
        """Join each work to its authors."""
        works = self.catalog_frame()
        if works.empty:
            return works
        exploded = works.explode("author_keys")
        authors = self._author_frame()
        if authors.empty:
            exploded["name"] = pd.NA
            return exploded
        return exploded.merge(
            authors,
            how="left",
            left_on="author_keys",
            right_on="author_key",
        )

    def top_publication_years(self, limit: int = 10) -> pd.DataFrame:
        """Count works for the most common publication years."""
        years = self.catalog_frame().dropna(subset=["publication_year"])
        if years.empty:
            return pd.DataFrame(columns=["publication_year", "work_count"])
        summary = (
            years.groupby("publication_year", dropna=True)
            .size()
            .reset_index(name="work_count")
            .sort_values(["work_count", "publication_year"], ascending=[False, True])
        )
        return summary.head(limit).reset_index(drop=True)

    def top_languages(self, limit: int = 10) -> pd.DataFrame:
        """Count works for the most common languages, including unknown."""
        languages = self.catalog_frame().explode("languages")
        if languages.empty:
            return pd.DataFrame(columns=["language", "work_count"])
        languages["language"] = languages["languages"].map(_fill_language)
        summary = (
            languages.groupby("language", dropna=False)
            .size()
            .reset_index(name="work_count")
            .sort_values(["work_count", "language"], ascending=[False, True])
        )
        return summary.head(limit).reset_index(drop=True)

    def performance_metrics(self) -> pd.DataFrame:
        """Return one row per catalog metric for the report workbook."""
        frame = self.catalog_frame()
        languages = frame.explode("languages")
        language_names = languages["languages"].map(_fill_language)
        known_languages = language_names.loc[language_names != "unknown"]
        years = frame["publication_year"].dropna()
        metrics = {
            "work_count": int(len(frame)),
            "author_count": int(self._author_frame()["author_key"].nunique())
            if not self._author_frame().empty
            else 0,
            "missing_year_count": int(frame["publication_year"].isna().sum()),
            "known_language_count": int(known_languages.nunique()),
            "earliest_year": int(years.min()) if not years.empty else None,
            "latest_year": int(years.max()) if not years.empty else None,
        }
        return pd.DataFrame(
            [{"metric": name, "value": value} for name, value in metrics.items()]
        )

    def _author_frame(self) -> pd.DataFrame:
        frame = self._authors.copy()
        if frame.empty:
            return pd.DataFrame(columns=["author_key", "name"])
        if "author_key" not in frame.columns:
            frame["author_key"] = pd.NA
        if "name" not in frame.columns:
            frame["name"] = pd.NA
        return frame.loc[:, ["author_key", "name"]]


def _fill_title(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Unknown"
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return "Unknown"
    return text


def _fill_language(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "unknown"
    text = str(value).strip().casefold()
    if not text or text == "nan":
        return "unknown"
    return text


def _string_items(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _publication_year(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2100 else None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return year if 1000 <= year <= 2100 else None
    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return int(parsed.year)
    match = _YEAR.search(text)
    if match is None:
        return None
    return int(match.group(1))
