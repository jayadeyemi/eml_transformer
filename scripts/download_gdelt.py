from __future__ import annotations

import zipfile
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


GKG_COLUMNS = [
    "GKGRECORDID", "DATE", "SourceCollectionIdentifier",
    "SourceCommonName", "DocumentIdentifier", "Counts", "V2Counts",
    "Themes", "V2Themes", "Locations", "V2Locations", "Persons",
    "V2Persons", "Organizations", "V2Organizations", "Tone", "Dates",
    "GCAM", "SharingImage", "RelatedImages", "SocialImageEmbeds",
    "SocialVideoEmbeds", "Quotations", "AllNames", "Amounts",
    "TranslationInfo", "Extras",
]


START_DATE = "2025-01-01"
END_DATE = "2025-12-31"

DAYS_PER_MONTH = 3
FILES_PER_DAY = 4  # each file = 15 minutes, so 4 = first hour of the day

OUT_DIR = Path("data/samples/")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def stratified_sample_dates(
    start_date: str,
    end_date: str,
    days_per_month: int = 3,
) -> list[str]:
    """
    Pick evenly spaced sample days from each month.
    Example with 3 days/month: early, middle, late month.
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    months = pd.period_range(start=start, end=end, freq="M")
    sample_dates: list[str] = []

    for month in months:
        month_start = max(month.start_time, start)
        month_end = min(month.end_time, end)

        possible_days = pd.date_range(month_start, month_end, freq="D")

        if len(possible_days) <= days_per_month:
            selected_days = possible_days
        else:
            indexes = [
                round(i * (len(possible_days) - 1) / (days_per_month - 1))
                for i in range(days_per_month)
            ]
            selected_days = [possible_days[i] for i in indexes]

        sample_dates.extend(
            d.strftime("%Y-%m-%d") for d in selected_days
        )

    return sample_dates


def timestamps_for_day(date: str, files_per_day: int = 4) -> list[str]:
    """
    GDELT GKG v2 files are available every 15 minutes.
    """
    start = datetime.strptime(date, "%Y-%m-%d")

    return [
        (start + timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        for i in range(files_per_day)
    ]


def load_gkg_file(timestamp: str) -> pd.DataFrame | None:
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


def download_stratified_gdelt_sample() -> pd.DataFrame:
    sample_dates = stratified_sample_dates(
        START_DATE,
        END_DATE,
        days_per_month=DAYS_PER_MONTH,
    )

    print(f"Sample dates: {len(sample_dates)}")
    print(sample_dates)

    frames: list[pd.DataFrame] = []

    for date in sample_dates:
        timestamps = timestamps_for_day(date, files_per_day=FILES_PER_DAY)

        for timestamp in timestamps:
            print(f"Downloading {timestamp}")
            df = load_gkg_file(timestamp)

            if df is not None and not df.empty:
                frames.append(df)

    if not frames:
        raise RuntimeError("No GDELT files were downloaded successfully.")

    combined = pd.concat(frames, ignore_index=True)

    output_path = OUT_DIR / (
        f"gkg_sample_{START_DATE}_to_{END_DATE}_"
        f"{DAYS_PER_MONTH}days_per_month_"
        f"{FILES_PER_DAY}files_per_day.parquet"
    )

    combined.to_parquet(output_path, index=False)

    print(f"Saved {len(combined):,} rows to {output_path}")

    return combined




if __name__ == "__main__":
    df = download_stratified_gdelt_sample()
