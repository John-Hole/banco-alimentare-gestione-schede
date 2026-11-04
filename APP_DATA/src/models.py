"""Modelli dati Pydantic per l'applicazione Banco Alimentare."""

from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, field_validator


class FlagSeverity(str, Enum):
    GREEN = "GREEN"
    WARNING = "WARNING"
    RED = "RED"


class ValidationFlag(BaseModel):
    severity: FlagSeverity
    field: str
    message: str


class BoxItem(BaseModel):
    """Singola scatola chiusa indicata sulla scheda corriere."""
    categoria: str
    kg: float = Field(ge=0.0)
    confermata: bool = False



class DeclaredTotals(BaseModel):
    """Eventuali totali scritti a mano in fondo alla scheda corriere."""
    kg_totale: Optional[float] = None
    scatole_totale: Optional[int] = None
    per_categoria: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("per_categoria", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v if isinstance(v, dict) else {}

class RawCardExtraction(BaseModel):
    """Output grezzo estratto dalla visione IA o scanner."""
    supermercato: Optional[str] = None
    numero_scheda: Optional[str] = None  # es: "1", "A1", "NON_PRESENTE", "ILLEGGIBILE"
    righe_scatole: List[BoxItem] = Field(default_factory=list)
    totali_dichiarati: DeclaredTotals = Field(default_factory=DeclaredTotals)
    note_o_anomalie: Optional[str] = None
    scheda_capovolta: bool = False
    was_rotated_180: bool = False
    tempo_elaborazione_sec: Optional[float] = None
    costo_usd: Optional[float] = None

    @property
    def scatole(self) -> List[BoxItem]:
        """Alias retrocompatibile per righe_scatole."""
        return self.righe_scatole

    @property
    def totali_dichiarati_kg(self) -> Dict[str, Optional[float]]:
        """Mappa di comodità {categoria: kg_dichiarati} da totali_dichiarati.per_categoria."""
        if not self.totali_dichiarati or not self.totali_dichiarati.per_categoria:
            return {}
        result: Dict[str, Optional[float]] = {}
        for cat, val in self.totali_dichiarati.per_categoria.items():
            if isinstance(val, dict):
                kg_val = val.get("kg")
                try:
                    result[cat] = float(kg_val) if kg_val is not None else None
                except (ValueError, TypeError):
                    result[cat] = None
            elif isinstance(val, (int, float)):
                result[cat] = float(val)
        return result


class CategoryDetail(BaseModel):
    """Riepilogo e confronto per una delle 13 categorie canoniche."""
    categoria: str
    kg_calcolati: float = 0.0
    scatole_calcolate: int = 0
    kg_dichiarati: Optional[float] = None
    scatole_dichiarate: Optional[int] = None
    kg_col: int
    box_col: int
    flags: List[ValidationFlag] = Field(default_factory=list)
    confermata: bool = False


class ProcessedCard(BaseModel):
    """Scheda corriere completamente elaborata e validata con flag."""
    supermercato_rilevato: str = ""
    supermercato_confermato: str = ""
    numero_scheda_rilevato: Optional[str] = None
    numero_scheda_risolto: Optional[str] = None  # es: "A1"
    righe_scatole: List[BoxItem] = Field(default_factory=list)
    totali_dichiarati: DeclaredTotals = Field(default_factory=DeclaredTotals)
    categorie: Dict[str, CategoryDetail] = Field(default_factory=dict)
    totale_kg_calcolati: float = 0.0
    totale_scatole_calcolate: int = 0
    flags: List[ValidationFlag] = Field(default_factory=list)
    ha_flag_rossi: bool = False
    autorizzato_da_operatore: bool = False
    pronto_per_scrittura: bool = False
