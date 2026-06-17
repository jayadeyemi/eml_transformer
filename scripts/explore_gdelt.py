import pandas as pd
import zipfile
import requests
from io import BytesIO
from urllib.parse import urlparse
from datetime import datetime, timedelta

START_DATE = "2025-01-01"
END_DATE = "2025-03-01"

GKG_COLUMNS = [
    "GKGRECORDID", "DATE", "SourceCollectionIdentifier",
    "SourceCommonName", "DocumentIdentifier", "Counts", "V2Counts",
    "Themes", "V2Themes", "Locations", "V2Locations", "Persons",
    "V2Persons", "Organizations", "V2Organizations", "Tone", "Dates",
    "GCAM", "SharingImage", "RelatedImages", "SocialImageEmbeds",
    "SocialVideoEmbeds", "Quotations", "AllNames", "Amounts",
    "TranslationInfo", "Extras",
]

CORE_THEMES = {
    "NATURAL_DISASTER_EXTREME_WEATHER",
    "NATURAL_DISASTER_SEVERE_WEATHER",
    "NATURAL_DISASTER_FLOODING",
    "NATURAL_DISASTER_HURRICANE",
    "NATURAL_DISASTER_TORNADO",
    "NATURAL_DISASTER_WILDFIRE",
    "POWER_OUTAGE",
    "MANMADE_DISASTER_POWER_OUTAGE",
    "MANMADE_DISASTER_POWER_OUTAGES",
    "MANMADE_DISASTER_WITHOUT_POWER",
    "MANMADE_DISASTER_WITHOUT_ELECTRICITY",
    "ECON_ELECTRICALGRID",
    "ENV_WINDPOWER",
    "WB_508_POWER_SYSTEMS",
    "WB_527_HYDROPOWER",
    "ENV_NUCLEARPOWER",
    "WB_515_POWER_SECTOR_POLICY_AND_INSTITUTIONS",
    "MANMADE_DISASTER_POWER_FAILURE",
    "WB_511_COAL_FIRED_POWER",
    "WB_1753_GAS_TO_POWER",
    "WB_513_OIL_FIRED_POWER",
    "MANMADE_DISASTER_RESTORE_POWER",
    "NATURAL_DISASTER_POWERFUL_STORM",
    "MANMADE_DISASTER_DOWNED_POWER_LINES",
    "MANMADE_DISASTER_KNOCKED_OUT_POWER",
    "WB_510_POWER_TRANSMISSION",
    "WB_1703_POWER_DISTRIBUTION",
    "ECON_NEWPOWERPLANT"
    "MANMADE_DISASTER_POWER_FAILURES",
    "WB_526_RENEWABLE_ENERGY_POLICY_AND_REGULATION",
    "WB_1757_ENERGY_FINANCE",
    "WB_1033_SOLAR_POWER_LAW_AND_REGULATION",
    "WB_1871_HYDROPOWER_LAW_AND_REGULATION",
    "WB_1034_WIND_POWER_LAW_AND_REGULATION"
    
}

URL_KEYWORDS = {
    "storm", "flood", "flooding", "hurricane", "tornado",
    "wildfire", "blizzard", "outage", "blackout",
    "power-outage", "power_outage", "without-power",
}

BAD_DOMAINS = {
    "slashfilm.com", "screenrant.com", "collider.com",
    "people.com", "tmz.com", "variety.com",
    "hollywoodreporter.com", "deadline.com", "ew.com",
    "thewrap.com", "cinemablend.com", "comicbook.com",
}


def timestamps_for_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    days = (end - start).days + 1
    return [
        (start + timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S")
        for i in range(96 * days)
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

        df.columns = GKG_COLUMNS[:len(df.columns)]
        return df

    except Exception as e:
        print(f"Failed {timestamp}: {e}")
        return None


def parse_themes(value: str) -> set[str]:
    if pd.isna(value):
        return set()

    return {
        theme.strip().upper()
        for theme in value.split(";")
        if theme.strip()
    }


def is_us_location(value: str) -> bool:
    if pd.isna(value):
        return False

    return any(
        "#US#" in part or "United States" in part
        for part in value.split(";")
    )


def url_has_keyword(value: str) -> bool:
    if pd.isna(value):
        return False

    value = value.lower()
    return any(keyword in value for keyword in URL_KEYWORDS)


def clean_domain(value: str) -> str:
    if pd.isna(value) or not value:
        return ""

    domain = urlparse(value).netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def filter_gkg(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["_theme_set"] = df["Themes"].apply(parse_themes)
    df["_matched_themes"] = df["_theme_set"].apply(
        lambda s: sorted(s & CORE_THEMES)
    )
    df["_theme_count"] = df["_matched_themes"].apply(len)

    theme_mask = df["_theme_count"] >= 2
    us_mask = df["Locations"].apply(is_us_location)

    df["_domain"] = df["DocumentIdentifier"].apply(clean_domain)
    domain_mask = ~df["_domain"].isin(BAD_DOMAINS)

    filtered = df[
        theme_mask &
        us_mask &
        domain_mask
    ].copy()

    return filtered


all_filtered = []
raw_rows = 0

for timestamp in timestamps_for_range(START_DATE, END_DATE):
    print(f"Processing {timestamp}")

    df = load_gkg_file(timestamp)

    if df is None or df.empty:
        continue

    raw_rows += len(df)

    filtered = filter_gkg(df)

    if not filtered.empty:
        all_filtered.append(filtered)

if all_filtered:
    daily = pd.concat(all_filtered, ignore_index=True)
else:
    daily = pd.DataFrame()

if not daily.empty:
    daily = (
        daily
        .dropna(subset=["DocumentIdentifier"])
        .drop_duplicates(subset=["DocumentIdentifier"])
    )

print("\nSummary")
print(f"Raw rows processed: {raw_rows:,}")
print(f"Filtered articles: {len(daily):,}")

if not daily.empty:
    print(f"Unique domains: {daily['_domain'].nunique():,}")

    print("\nMatched theme counts:")
    print(
        daily.explode("_matched_themes")["_matched_themes"]
        .value_counts()
        .to_string()
    )

    print("\nTop domains:")
    print(
        daily["_domain"]
        .value_counts()
        .head(30)
        .to_string()
    )

    sample = daily[
        [
            "DATE",
            "_domain",
            "SourceCommonName",
            "DocumentIdentifier",
            "_matched_themes",
            "Themes",
            "Locations",
            "Organizations",
            "Tone",
        ]
    ].sample(
        min(100, len(daily)),
        random_state=42,
    )

    print("\nSample URLs:")
    for _, row in sample.iterrows():
        print("=" * 120)
        print("DATE:", row["DATE"])
        print("DOMAIN:", row["_domain"])
        print("SOURCE:", row["SourceCommonName"])
        print("URL:", row["DocumentIdentifier"])
        print("MATCHED THEMES:", row["_matched_themes"])
        print("LOCATIONS:", row["Locations"])
        print("TONE:", row["Tone"])

    output_path = f"gdelt_theme_large_test_{START_DATE}_{END_DATE}.csv"
    daily.drop(columns=["_theme_set"], errors="ignore").to_csv(output_path, index=False)

    print(f"\nSaved: {output_path}")
else:
    print("No articles matched the filter.")