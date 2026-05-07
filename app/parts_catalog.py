"""Curated list of high-resale parts per make.

Each entry has:
  name:             display name
  query_template:   eBay search string with {year} and {model} placeholders
  shipping_est_usd: estimated shipping cost in USD (used for net-value calc)
  models_filter:    optional list of model substrings the part applies to
                    (None = applies to every model of that make)

Parts are ordered by expected resale priority:
  Powertrain -> Electronics -> Safety -> Wheels -> Interior -> Exterior -> Long Tail

Edit freely. The pipeline will skip parts whose models_filter doesn't match
the vehicle's model string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CatalogPart:
    name: str
    query_template: str
    shipping_est_usd: float = 20.0
    models_filter: Optional[tuple[str, ...]] = None


# ---------------------------------------------------------------------------
# BMW
# ---------------------------------------------------------------------------

BMW_PARTS: tuple[CatalogPart, ...] = (

    # --- Powertrain ---
    CatalogPart("Engine (Long Block)",
                "{year} BMW {model} engine long block oem",
                shipping_est_usd=350.0),
    CatalogPart("DCT / SMG Transmission",
                "{year} BMW {model} DCT SMG transmission",
                shipping_est_usd=200.0,
                models_filter=("M3", "M5", "M6", "Z4", "135", "335")),
    CatalogPart("Differential (Rear)",
                "{year} BMW {model} rear differential",
                shipping_est_usd=120.0),
    CatalogPart("Turbocharger",
                "{year} BMW {model} turbocharger oem",
                shipping_est_usd=45.0),
    CatalogPart("Catalytic Converter",
                "{year} BMW {model} catalytic converter",
                shipping_est_usd=35.0),
    CatalogPart("HPFP (High Pressure Fuel Pump)",
                "{year} BMW {model} HPFP high pressure fuel pump",
                shipping_est_usd=15.0,
                models_filter=("335", "535", "135", "X3", "X5", "X6", "Z4")),

    # --- Electronics ---
    CatalogPart("DME / ECU",
                "{year} BMW {model} DME ECU",
                shipping_est_usd=12.0),
    CatalogPart("iDrive Screen (CIC/NBT)",
                "{year} BMW {model} iDrive screen CIC NBT",
                shipping_est_usd=20.0),
    CatalogPart("Instrument Cluster",
                "{year} BMW {model} instrument cluster speedometer",
                shipping_est_usd=15.0),
    CatalogPart("Parking Sensor Module / PDC",
                "{year} BMW {model} parking sensor PDC module",
                shipping_est_usd=12.0),
    CatalogPart("TPMS Sensors Set",
                "{year} BMW {model} TPMS tire pressure sensor set oem",
                shipping_est_usd=12.0),

    # --- Safety ---
    CatalogPart("ABS Module / Pump",
                "{year} BMW {model} ABS module pump",
                shipping_est_usd=15.0),
    CatalogPart("Airbag Module (SRS)",
                "{year} BMW {model} airbag SRS module",
                shipping_est_usd=15.0),

    # --- Wheels ---
    CatalogPart("Wheel Set",
                "{year} BMW {model} wheels set oem",
                shipping_est_usd=100.0),

    # --- Interior ---
    CatalogPart("M Sport Steering Wheel",
                "{year} BMW {model} M sport steering wheel",
                shipping_est_usd=18.0),
    CatalogPart("Sport Seats",
                "{year} BMW {model} sport seats",
                shipping_est_usd=120.0),
    CatalogPart("Carbon Fiber Trim",
                "{year} BMW {model} carbon fiber interior trim",
                shipping_est_usd=25.0,
                models_filter=("M3", "M4", "M5", "M6", "335", "535", "Z4")),
    CatalogPart("OEM Floor Mats",
                "{year} BMW {model} OEM floor mats set",
                shipping_est_usd=12.0),

    # --- Exterior ---
    CatalogPart("Adaptive Xenon Headlight",
                "{year} BMW {model} adaptive xenon headlight oem",
                shipping_est_usd=30.0),
    CatalogPart("LED Tail Light",
                "{year} BMW {model} LED tail light oem",
                shipping_est_usd=25.0),
    CatalogPart("Kidney Grille",
                "{year} BMW {model} kidney grille",
                shipping_est_usd=25.0),

    # --- Long Tail ---
    CatalogPart("Center Cap Set",
                "{year} BMW {model} center cap wheel hub cap set oem",
                shipping_est_usd=10.0),
    CatalogPart("M Badge / Emblem",
                "BMW M badge emblem roundel oem",
                shipping_est_usd=8.0),
    CatalogPart("Mirror Cap",
                "{year} BMW {model} mirror cap cover oem",
                shipping_est_usd=10.0),
    CatalogPart("Antenna Base",
                "{year} BMW {model} antenna base oem",
                shipping_est_usd=8.0),
)


# ---------------------------------------------------------------------------
# Volkswagen
# ---------------------------------------------------------------------------

VW_PARTS: tuple[CatalogPart, ...] = (

    # --- Powertrain ---
    CatalogPart("Engine (Long Block)",
                "{year} Volkswagen {model} engine long block oem",
                shipping_est_usd=350.0),
    CatalogPart("DSG Transmission",
                "{year} Volkswagen {model} DSG transmission",
                shipping_est_usd=200.0,
                models_filter=("GTI", "Golf", "Jetta", "Passat", "R32", "R", "Tiguan", "CC")),
    CatalogPart("Turbocharger (K03/K04)",
                "{year} Volkswagen {model} turbocharger K03 K04",
                shipping_est_usd=45.0),
    CatalogPart("Catalytic Converter",
                "{year} Volkswagen {model} catalytic converter",
                shipping_est_usd=35.0),
    CatalogPart("APR / Cold Air Intake",
                "{year} Volkswagen {model} cold air intake APR",
                shipping_est_usd=25.0),

    # --- Electronics ---
    CatalogPart("ECU / TCM",
                "{year} Volkswagen {model} ECU TCM module",
                shipping_est_usd=12.0),
    CatalogPart("RNS Navigation Head Unit",
                "{year} Volkswagen {model} RNS 510 315 navigation",
                shipping_est_usd=18.0),
    CatalogPart("Instrument Cluster",
                "{year} Volkswagen {model} instrument cluster speedometer",
                shipping_est_usd=15.0),
    CatalogPart("Parking Sensor Module / PDC",
                "{year} Volkswagen {model} parking sensor PDC module",
                shipping_est_usd=12.0),

    # --- Safety ---
    CatalogPart("ABS Module / Pump",
                "{year} Volkswagen {model} ABS module pump",
                shipping_est_usd=15.0),
    CatalogPart("Airbag Module (SRS)",
                "{year} Volkswagen {model} airbag SRS module",
                shipping_est_usd=15.0),

    # --- Wheels ---
    CatalogPart("Wheel Set",
                "{year} Volkswagen {model} wheels set oem",
                shipping_est_usd=100.0),

    # --- Interior ---
    CatalogPart("Sport Steering Wheel",
                "{year} Volkswagen {model} sport multifunction steering wheel",
                shipping_est_usd=18.0),
    CatalogPart("Sport / Recaro Seats",
                "{year} Volkswagen {model} sport seats Recaro",
                shipping_est_usd=120.0,
                models_filter=("GTI", "R32", "R", "Golf", "Jetta")),
    CatalogPart("OEM Floor Mats",
                "{year} Volkswagen {model} OEM floor mats set",
                shipping_est_usd=12.0),

    # --- Exterior ---
    CatalogPart("Xenon Headlight Assembly",
                "{year} Volkswagen {model} xenon headlight oem",
                shipping_est_usd=30.0),
    CatalogPart("LED / OEM Tail Light",
                "{year} Volkswagen {model} tail light oem",
                shipping_est_usd=25.0),
    CatalogPart("Front Grille",
                "{year} Volkswagen {model} grille R-Line GTI",
                shipping_est_usd=25.0),

    # --- Long Tail ---
    CatalogPart("VW Emblem / Badge",
                "Volkswagen VW emblem badge oem",
                shipping_est_usd=8.0),
    CatalogPart("Door Handle",
                "{year} Volkswagen {model} door handle exterior oem",
                shipping_est_usd=8.0),
    CatalogPart("Mirror Cap",
                "{year} Volkswagen {model} mirror cap cover oem",
                shipping_est_usd=10.0),
    CatalogPart("Center Cap Set",
                "{year} Volkswagen {model} center cap wheel hub cap set oem",
                shipping_est_usd=10.0),
)


# ---------------------------------------------------------------------------
# Mercedes-Benz
# ---------------------------------------------------------------------------

MERCEDES_PARTS: tuple[CatalogPart, ...] = (

    # --- Powertrain ---
    CatalogPart("Engine (Long Block)",
                "{year} Mercedes-Benz {model} engine long block oem",
                shipping_est_usd=350.0),
    CatalogPart("Transmission (7G/9G-Tronic)",
                "{year} Mercedes-Benz {model} transmission automatic",
                shipping_est_usd=200.0),
    CatalogPart("Transfer Case (4MATIC)",
                "{year} Mercedes-Benz {model} transfer case 4MATIC",
                shipping_est_usd=150.0,
                models_filter=("GL", "ML", "GLE", "GLC", "GLK", "G", "4MATIC")),
    CatalogPart("Turbocharger",
                "{year} Mercedes-Benz {model} turbocharger oem",
                shipping_est_usd=45.0),
    CatalogPart("Supercharger (AMG)",
                "{year} Mercedes-Benz {model} supercharger AMG",
                shipping_est_usd=80.0,
                models_filter=("AMG", "C63", "E63", "S63", "CLS63", "SL63")),
    CatalogPart("Catalytic Converter",
                "{year} Mercedes-Benz {model} catalytic converter",
                shipping_est_usd=35.0),
    CatalogPart("HPFP (High Pressure Fuel Pump)",
                "{year} Mercedes-Benz {model} high pressure fuel pump",
                shipping_est_usd=15.0),

    # --- Electronics ---
    CatalogPart("ECU / TCM",
                "{year} Mercedes-Benz {model} ECU TCM module",
                shipping_est_usd=12.0),
    CatalogPart("COMAND Head Unit (NTG)",
                "{year} Mercedes-Benz {model} COMAND NTG navigation",
                shipping_est_usd=20.0),
    CatalogPart("Instrument Cluster",
                "{year} Mercedes-Benz {model} instrument cluster speedometer",
                shipping_est_usd=15.0),
    CatalogPart("Parking Sensor Module / PDC",
                "{year} Mercedes-Benz {model} parking sensor PDC module",
                shipping_est_usd=12.0),
    CatalogPart("TPMS Sensors Set",
                "{year} Mercedes-Benz {model} TPMS tire pressure sensor set oem",
                shipping_est_usd=12.0),
    CatalogPart("Airmatic / Air Suspension Compressor",
                "{year} Mercedes-Benz {model} air suspension compressor Airmatic",
                shipping_est_usd=25.0,
                models_filter=("S", "E", "GL", "ML", "GLE", "CLS", "SL")),

    # --- Safety ---
    CatalogPart("ABS Module / Pump",
                "{year} Mercedes-Benz {model} ABS module pump",
                shipping_est_usd=15.0),
    CatalogPart("Airbag Module (SRS)",
                "{year} Mercedes-Benz {model} airbag SRS module",
                shipping_est_usd=15.0),

    # --- Wheels ---
    CatalogPart("AMG Wheel Set",
                "{year} Mercedes-Benz {model} AMG wheels set oem",
                shipping_est_usd=100.0),

    # --- Interior ---
    CatalogPart("AMG Steering Wheel",
                "{year} Mercedes-Benz {model} AMG steering wheel",
                shipping_est_usd=18.0),
    CatalogPart("Sport / AMG Seats",
                "{year} Mercedes-Benz {model} AMG sport seats",
                shipping_est_usd=120.0,
                models_filter=("AMG", "C63", "E63", "C", "E", "CLA", "CLS")),
    CatalogPart("OEM Floor Mats",
                "{year} Mercedes-Benz {model} OEM floor mats set",
                shipping_est_usd=12.0),

    # --- Exterior ---
    CatalogPart("Xenon / LED Headlight",
                "{year} Mercedes-Benz {model} xenon LED headlight oem",
                shipping_est_usd=30.0),
    CatalogPart("LED Tail Light",
                "{year} Mercedes-Benz {model} LED tail light oem",
                shipping_est_usd=25.0),
    CatalogPart("AMG Front Bumper / Splitter",
                "{year} Mercedes-Benz {model} AMG front bumper",
                shipping_est_usd=80.0,
                models_filter=("AMG", "C63", "E63", "CLA45", "A45")),

    # --- Long Tail ---
    CatalogPart("Star Emblem / Hood Ornament",
                "Mercedes-Benz star emblem hood ornament oem",
                shipping_est_usd=8.0),
    CatalogPart("Mirror Cap",
                "{year} Mercedes-Benz {model} mirror cap cover oem",
                shipping_est_usd=10.0),
    CatalogPart("Center Cap Set",
                "{year} Mercedes-Benz {model} center cap wheel hub cap set oem",
                shipping_est_usd=10.0),
)


CATALOG: dict[str, tuple[CatalogPart, ...]] = {
    "BMW": BMW_PARTS,
    "VOLKSWAGEN": VW_PARTS,
    "MERCEDES-BENZ": MERCEDES_PARTS,
}


def parts_for_vehicle(make: str, model: str) -> list[CatalogPart]:
    """Return the catalog entries that apply to a given vehicle."""
    if not make:
        return []
    parts = CATALOG.get(make.strip().upper(), ())
    model_norm = (model or "").upper()
    out: list[CatalogPart] = []
    for p in parts:
        if p.models_filter is None:
            out.append(p)
            continue
        if any(tok.upper() in model_norm for tok in p.models_filter):
            out.append(p)
    return out
