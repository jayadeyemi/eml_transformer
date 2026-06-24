from typing import Any

import requests
import zipfile
from io import BytesIO
import pandas as pd 
from datetime import datetime, timedelta
from datetime import datetime, timezone
import re 

from concurrent.futures import ThreadPoolExecutor, as_completed

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

        logger.debug("target_themes=%d", len(self.target_themes))
        logger.debug("target_organizations=%d", len(self.target_organizations))
        logger.debug("target_locations=%d", len(self.target_locations))
       


    def fetch_records(
        self,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        logger.info(
            "Starting GDELT fetch | from_date=%s | to_date=%s",
            from_date,
            to_date,
        )

        timestamps = self._get_timestamps(from_date, to_date)

        logger.info(
            "Generated GDELT timestamps | files_to_download=%d",
            len(timestamps),
        )

        filtered_records, total_records_seen = self._get_records(timestamps)

        logger.info(
            "Finished GDELT fetch | raw_records=%d | filtered_records=%d | removed=%d",
            total_records_seen,
            len(filtered_records),
            total_records_seen - len(filtered_records),
        )

        return filtered_records.to_dict(orient="records")
    

    def standardize_record(self, record: dict[str, Any]) -> TextRecord:
        record_id = str(record["GKGRECORDID"])

        published_at = self._parse_gdelt_timestamp(
            record.get("DATE")
        )

        themes = [
            theme.strip()
            for theme in str(record.get("Themes", "")).split(";")
            if theme.strip()
        ]

        organizations = [
            org.strip()
            for org in str(record.get("Organizations", "")).split(";")
            if org.strip()
        ]

        persons = [
            person.strip()
            for person in str(record.get("Persons", "")).split(";")
            if person.strip()
        ]

        locations = list(self._parse_locations(
            record.get("Locations", "")
        ))

        return TextRecord(
            record_id=record_id,
            source=self.name,
            source_type=self.source_type,
            title=self._extract_page_title(record),
            text="",  # article text added after scraping
            published_at=published_at,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            url=record.get("DocumentIdentifier"),
            region=locations[0] if locations else None,
            categories=themes,
            metadata={
                "source_common_name": record.get("SourceCommonName"),
                "gdelt_timestamp": record.get("GDELT_TIMESTAMP"),
                "organizations": organizations,
                "persons": persons,
                "locations": locations,
                "tone": record.get("Tone"),
                "theme_match": record.get("theme_match"),
                "organization_match": record.get("organization_match"),
                "location_match": record.get("location_match"),
                "filter_match_count": record.get("filter_match_count"),
            },
            raw=record,
        )
    
    
    def _filter_records(
        self,
        records: pd.DataFrame,
    ) -> pd.DataFrame:
        if records.empty:
            logger.info("Skipping GDELT filtering | records=0")
            return records

        records = records.copy()

        records["theme_match"] = self._filter_themes(records, required_themes=self.min_filter_matches)
        records["organization_match"] = self._filter_organizations(records)
        records["location_match"] = self._filter_locations(records)

        # Two filter mechanics 

        # 1. require any two match type 
        # match_columns = [
        #     "theme_match",
        #     "organization_match",
        #     "location_match",
        # ]

        # records["filter_match_count"] = records[match_columns].sum(axis=1)

        # filtered = records.loc[
        #     records["filter_match_count"] >= self.min_filter_matches
        # ]


        # 2. filtering based on n theme matches and location or organization match
        filter_criteria = (records["theme_match"] & records["location_match"]) | (records["organization_match"])
     
        filtered = records.loc[
            filter_criteria
        ]

        logger.debug(
            "Filtered GDELT records | input=%d | output=%d | removed=%d | min theme matches=%d",
            len(records),
            len(filtered),
            len(records) - len(filtered),
            self.min_filter_matches,
        )

        logger.debug(
            "GDELT filter matches | theme=%d | organization=%d | location=%d",
            int(records["theme_match"].sum()),
            int(records["organization_match"].sum()),
            int(records["location_match"].sum()),
        )

        # logger.info(
        #     "GDELT match count distribution | counts=%s",
        #     records["filter_match_count"].value_counts().sort_index().to_dict(),
        # )

        return filtered

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
        required_themes: int=1,
    ) -> pd.Series:
        
        theme_count = records["Themes"].apply(
            lambda value: len(
                self._parse_themes(value) & self.target_themes
            )
        )

        return theme_count >= required_themes


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
    
    def _parse_locations(self, value) -> set[str]:
        if pd.isna(value):
            return set()

        parsed_locations: set[str] = set()

        for location in str(value).split(";"):
            parts = location.split("#")

            country_code = parts[2].strip().upper() if len(parts) > 2 else ""
            adm1_code = parts[3].strip().upper() if len(parts) > 3 else ""

            if country_code:
                parsed_locations.add(country_code)

            if adm1_code:
                parsed_locations.add(adm1_code)

            if country_code and adm1_code:
                parsed_locations.add(f"{country_code}-{adm1_code}")

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

    def _extract_page_title(self, record: dict) -> str:
        extras = record.get("Extras", "")

        match = re.search(
            r"<PAGE_TITLE>(.*?)</PAGE_TITLE>",
            extras,
            flags=re.DOTALL,
        )

        if match:
            return match.group(1).strip()

        return ''

    def _parse_gdelt_timestamp(self, timestamp: str) -> str:
        """
        Convert GDELT timestamp (YYYYMMDDHHMMSS)
        to ISO 8601 UTC string.
        """
        dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    
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
  
    def _get_records(
        self,
        timestamps: list[str],
    ) -> tuple[pd.DataFrame, int]:
        dfs = []
        failed = 0

        max_workers = min(8, len(timestamps))
        total_records_seen = 0

        logger.info(
            "Downloading GDELT files in parallel | files=%d | workers=%d",
            len(timestamps),
            max_workers,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._load_gkg_file, timestamp): timestamp
                for timestamp in timestamps
            }

            for i, future in enumerate(as_completed(futures), start=1):
                timestamp = futures[future]

                try:
                    filtered_df, total_records = future.result()
                except Exception:
                    logger.warning(
                        "GDELT download failed | timestamp=%s",
                        timestamp,
                        exc_info=True,
                    )
                    failed += 1
                    continue
                
                total_records_seen += total_records
                if filtered_df is not None and not filtered_df.empty:
                    dfs.append(filtered_df)

                logger.debug(
                    "GDELT download progress | completed=%d/%d | timestamp=%s | rows=%s",
                    i,
                    len(timestamps),
                    timestamp,
                    0 if filtered_df is None else len(filtered_df),
                )

        if not dfs:
            logger.warning(
                "No GDELT records loaded | files=%d | failed=%d",
                len(timestamps),
                failed,
            )
            return pd.DataFrame(columns=GKG_COLUMNS), total_records_seen

        combined = pd.concat(dfs, ignore_index=True)

        logger.info(
            "Finished parallel GDELT download | files=%d | failed=%d | records=%d",
            len(timestamps),
            failed,
            len(combined),
        )

        return combined, total_records_seen

    def _load_gkg_file(
        self,
        timestamp: str,
    ) -> tuple[pd.DataFrame | None ,  int]:
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
                    encoding="latin1",
                )

            df.columns = GKG_COLUMNS[: len(df.columns)]
            df["GDELT_TIMESTAMP"] = timestamp
            df["GDELT_URL"] = url


            filtered = self._filter_records(df)

            logger.debug(
                "Loaded GDELT file | timestamp=%s | rows=%d",
                timestamp,
                len(df),
            )

            return filtered, len(df)

        except Exception:
            logger.warning(
                "Failed to load GDELT file | timestamp=%s | url=%s",
                timestamp,
                url,
                exc_info=True,
            )
            return None, 0




    
