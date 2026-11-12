"""Registry of license data sources, one entry per jurisdiction.

To add a jurisdiction, add an entry here. Its files go under
``jurisdictions/<slug>/`` automatically.
"""

SOURCES = {
    "baltimore-county": {
        "name": "Baltimore County, MD",
        "kind": "arcgis",
        "base_url": "https://bcgisapps.baltimorecountymd.gov/arcgis/rest/services/RentalLicense/MapServer",
        # layer slug -> (ArcGIS layer id, record-id field)
        "layers": {
            "licenses": (0, "B1_RECORD_ID"),
            "exemptions": (1, "RECORD_ID"),
            "multifamily": (2, "RECORD_ID"),
        },
        # Unified column -> source field, used for the cross-jurisdiction
        # `license_records` table in the SQLite build. Missing fields become ''.
        "columns": {
            "house_no": "B1_HSE_NBR_START",
            "house_no_fraction": "B1_HSE_FRAC_NBR_START",
            "street_dir": "B1_STR_DIR",
            "street_name": "B1_STR_NAME",
            "street_suffix": "B1_STR_SUFFIX",
            "street_suffix_dir": "B1_STR_SUFFIX_DIR",
            "unit_type": "B1_UNIT_TYPE",
            "unit": "B1_UNIT_START",
            "city": "B1_SITUS_CITY",
            "state": "B1_SITUS_STATE",
            "zip": "B1_SITUS_ZIP",
            "parcel_id": "TAXPIN",
            "issue_date": "ISSUE_DATE",
            "expiration_date": "EXPIRATION_DATE",
            "status": "B1_APPL_STATUS",
            "exemption_reason": "B1_CHECKBOX_DESC",
            "total_units": "TOTALUNITS",
            "complex_name": "APARTMENTCOMPLEX",
            "lon": "LON",
            "lat": "LAT",
        },
    },
}

UNIFIED_COLUMNS = list(SOURCES["baltimore-county"]["columns"])

# Guardrails: a pull that trips these is refused (nothing is written) unless --force.
MAX_ROW_COUNT_CHANGE = 0.05  # fractional change vs the previous pull of the same layer
MIN_ROWS_FOR_RATIO_CHECK = 100
