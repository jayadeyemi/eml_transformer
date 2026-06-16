from typing import Any

import requests
import zipfile
from io import BytesIO
import pandas as pd 
from datetime import datetime, timedelta



from eml_transformer.ingestion.base import TextSource
from eml_transformer.ingestion.registry import register_source
from eml_transformer.ingestion.schema import TextRecord, utc_now

import logging
logger = logging.getLogger(__name__)

GKG_COLUMNS = [
    "GKGRECORDID", "DATE", "SourceCollectionIdentifier",
    "SourceCommonName", "DocumentIdentifier", "Counts", "V2Counts",
    "Themes", "V2Themes", "Locations", "V2Locations", "Persons",
    "V2Persons", "Organizations", "V2Organizations", "Tone", "Dates",
    "GCAM", "SharingImage", "RelatedImages", "SocialImageEmbeds",
    "SocialVideoEmbeds", "Quotations", "AllNames", "Amounts",
    "TranslationInfo", "Extras",
]


@register_source("gdelt")
class GDELTSource(TextSource):
    name = 'gdelt'
    source_type = 'api'
    update_mode = 'incremental'
    supports_backfill = True 
    default_lookback_days = 1


    def __init__(
        self,
        target_themes: set[str] | None = None,
        target_locations: set[str] | None = None,
        target_organizations: set[str] | None = None,
        min_filter_matches: int = 2,
    ):
       
        self.target_themes = {
            value.upper()
            for value in (target_themes or set())
        }

        self.target_organizations = {
            value.upper()
            for value in (target_organizations or set())
        }

        self.target_locations = {
            value.upper()
            for value in (target_locations or set())
        }

        self.min_filter_matches = min_filter_matches


    def fetch_records(
        self,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        
        timestamps = self._get_timestamps(from_date, to_date)
        records  =  self._get_records(timestamps)
        filtered_df = self._filter_records(records)
        
        return filtered_df
    

    def standardize_record(self, record):
        return super().standardize_record(record)
    
    
    def _filter_records(
        self,
        records: pd.DataFrame,
    ) -> pd.DataFrame:
        records = records.copy()

        records["theme_match"] = self._filter_themes(records)
        records["organization_match"] = self._filter_organizations(records)
        records["location_match"] = self._filter_locations(records)

        match_columns = [
            "theme_match",
            "organization_match",
            "location_match",
        ]

        records["filter_match_count"] = records[match_columns].sum(axis=1)

        return records.loc[
            records["filter_match_count"] >= self.min_filter_matches
        ]

    def _parse_themes(
        self,
        value: Any,
    ) -> set[str]:
        if pd.isna(value):
            return set()

        return {
            theme.strip().upper()
            for theme in str(value).split(";")
            if theme.strip()
        }


    def _filter_themes(
        self,
        records: pd.DataFrame,
    ) -> pd.Series:
        return records["Themes"].apply(
            lambda value: bool(
                self._parse_themes(value) & self.target_themes
            )
        )

    def _parse_organizations(
        self,
        value: Any,
    ) -> set[str]:
        if pd.isna(value):
            return set()

        return {
            org.split(",", 1)[0].strip().upper()
            for org in str(value).split(";")
            if org.strip()
        }


    def _filter_organizations(
        self,
        records: pd.DataFrame,
    ) -> pd.Series:
        return records["V2Organizations"].apply(
            lambda value: bool(
                self._parse_organizations(value) & self.target_organizations
            )
        )
    
    def _parse_locations(
        self,
        value: Any,
    ) -> set[str]:
        if pd.isna(value):
            return set()

        parsed_locations: set[str] = set()

        for location in str(value).split(";"):
            location = location.strip()

            if not location:
                continue

            parts = location.split("#")

            # Always keep the full raw location too.
            parsed_locations.add(location.upper())

            # GDELT location fields often look roughly like:
            # type#full_name#country_code#adm1_code#lat#lon#feature_id
            if len(parts) > 1 and parts[1].strip():
                parsed_locations.add(parts[1].strip().upper())

            if len(parts) > 2 and parts[2].strip():
                parsed_locations.add(parts[2].strip().upper())

            if len(parts) > 3 and parts[3].strip():
                parsed_locations.add(parts[3].strip().upper())

        return parsed_locations


    def _filter_locations(
        self,
        records: pd.DataFrame,
    ) -> pd.Series:
        return records["V2Locations"].apply(
            lambda value: bool(
                self._parse_locations(value) & self.target_locations
            )
        )

    def _get_timestamps(self, from_date, to_date):
        timestamps = (
            pd.date_range(
                start=from_date,
                end=pd.to_datetime(to_date) + pd.Timedelta(days=1) - pd.Timedelta(minutes=15),
                freq="15min",
            )
            .strftime("%Y%m%d%H%M%S")
            .tolist()
        )
        return timestamps
  
    def _get_records(self, timestamps):
        dfs = []
        for timestamp in timestamps:
            df = self.load_gkg_file(timestamp)

            if df is not None and not df.empty:
                dfs.append(df)

        if not dfs:
            return pd.DataFrame(columns=GKG_COLUMNS)

        return pd.concat(dfs, ignore_index=True)

    def _load_gkg_file(timestamp: str) -> pd.DataFrame | None:
        url = f"http://data.gdeltproject.org/gdeltv2/{timestamp}.gkg.csv.zip"

        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()

            with zipfile.ZipFile(BytesIO(response.content)) as z:
                filename = z.namelist()[0]

                df = pd.read_csv(
                    z.open(filename),
                    sep="\t",
                    header=None,
                    dtype=str,
                    low_memory=False,
                )

            df.columns = GKG_COLUMNS[: len(df.columns)]
            df["GDELT_TIMESTAMP"] = timestamp
            df["GDELT_URL"] = url

            return df

        except Exception as e:
            print(f"Failed {timestamp}: {e}")
            return None




    
