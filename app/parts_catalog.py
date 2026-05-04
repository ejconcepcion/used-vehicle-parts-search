"""Curated list of high-resale parts per make.

Each entry has:
  name:            display name
  query_template:  eBay search string with {year} and {model} placeholders
  models_filter:   optional list of model substrings the part applies to
                   (None = applies to every model of that make)

Edit freely. The pipeline will skip parts whose models_filter doesn't match
the vehicle's model string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CatalogPart:
    name: str
    query_template: str
    models_filter: Optional[tuple[str, ...]] = None


# --- BMW -------------------------------------------------------------------

BMW_PARTS: tuple[CatalogPart, ...] = (
    CatalogPart("Adaptive Xenon Headlight",
                "{year} BMW {model} adaptive xenon headlight oem"),
    CatalogPart("LED Tail Light",
                "{year} BMW {model} LED tail light oem"),
    CatalogPart("DME / ECU",
                "{year} BMW {model} DME ECU"),
    CatalogPart("iDrive Screen (CIC/NBT)",
                "{year} BMW {model} iDrive screen CIC NBT"),
    CatalogPart("Catalytic Converter",
                "{year} BMW {model} catalytic converter"),
    CatalogPart("Turbocharger",
                "{year} BMW {model} turbocharger oem"),
    CatalogPart("M Sport Steering Wheel",
                "{year} BMW {model} M sport steering wheel"),
    CatalogPart("Wheel Set",
                "{year} BMW {model} wheels set oem"),
    CatalogPart("Differential (Rear)",
                "{year} BMW {model} rear differential"),
    CatalogPart("Kidney Grille",
                "{year} BMW {model} kidney grille"),
    CatalogPart("Sport Seats",
                "{year} BMW {model} sport seats"),
    CatalogPart("Carbon Fiber Trim",
                "{year} BMW {model} carbon fiber interior trim",
                models_filter=("M3", "M4", "M5", "M6", "335", "535", "Z4")),
    CatalogPart("DCT/SMG Transmission",
                "{year} BMW {model} DCT SMG transmission",
                models_filter=("M3", "M5", "M6", "Z4", "135", "335")),
    CatalogPart("HPFP (High Pressure Fuel Pump)",
                "{year} BMW {model} HPFP high pressure fuel pump",
                models_filter=("335", "535", "135", "X3", "X5", "X6", "Z4")),
)

# --- Volkswagen ------------------------------------------------------------

VW_PARTS: tuple[CatalogPart, ...] = (
    CatalogPart("Xenon Headlight Assembly",
                "{year} Volkswagen {model} xenon headlight oem"),
    CatalogPart("LED/OEM Tail Light",
                "{year} Volkswagen {model} tail light oem"),
    CatalogPart("ECU / TCM",
                "{year} Volkswagen {model} ECU TCM module"),
    CatalogPart("RNS Navigation Head Unit",
                "{year} Volkswagen {model} RNS 510 315 navigation"),
    CatalogPart("Catalytic Converter",
                "{year} Volkswagen {model} catalytic converter"),
    CatalogPart("Turbocharger (K03/K04)",
                "{year} Volkswagen {model} turbocharger K03 K04"),
    CatalogPart("DSG Transmission",
                "{year} Volkswagen {model} DSG transmission",
                models_filter=("GTI", "Golf", "Jetta", "Passat", "R32", "R", "Tiguan", "CC")),
    CatalogPart("Sport Steering Wheel",
                "{year} Volkswagen {model} sport multifunction steering wheel"),
    CatalogPart("Wheel Set",
                "{year} Volkswagen {model} wheels set oem"),
    CatalogPart("Front Grille",
                "{year} Volkswagen {model} grille R-Line GTI"),
    CatalogPart("Sport / Recaro Seats",
                "{year} Volkswagen {model} sport seats Recaro",
                models_filter=("GTI", "R32", "R", "Golf", "Jetta")),
    CatalogPart("APR / Cold Air Intake",
                "{year} Volkswagen {model} cold air intake APR"),
)


CATALOG: dict[str, tuple[CatalogPart, ...]] = {
    "BMW": BMW_PARTS,
    "VOLKSWAGEN": VW_PARTS,
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
