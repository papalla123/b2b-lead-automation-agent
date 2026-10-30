"""
lead_logic.py: normalización, validación y scoring de leads.

Solo usa la librería estándar, así que los tests corren sin red, sin API keys
y sin FastAPI.

Pipeline:
    dict crudo -> normalize_lead() -> [señales del LLM, opcional] -> qualify()

El tier lo decide siempre este código con puntos y umbrales. El LLM solo ayuda a
leer texto libre (presupuesto, urgencia) y a resumir; no puede fijar el tier.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Reglas de negocio: se ajustan aquí, sin tocar la lógica.
# --------------------------------------------------------------------------- #

# (presupuesto mínimo en USD, puntos), de mayor a menor.
BUDGET_BANDS = [(10_000, 50), (5_000, 40), (2_000, 30), (500, 18), (1, 8)]
BUDGET_UNKNOWN_POINTS = 10

URGENCY_POINTS = {
    "immediate": 50,     # hasta 7 días
    "this_month": 35,    # hasta 30 días
    "this_quarter": 20,  # hasta 90 días
    "unknown": 10,
    "exploring": 5,      # sin plazo, solo se está informando
}

HOT_MIN = 75   # score >= 75 -> HOT
WARM_MIN = 40  # score >= 40 -> WARM; por debajo, COLD
HIGH_BUDGET_USD = 5_000

# Tipos de cambio fijos a USD, solo para la demo. En producción usa una API de FX.
FX_TO_USD = {"USD": 1.0, "PEN": 0.27, "EUR": 1.08, "MXN": 0.055, "COP": 0.00025}

VALID_SOURCES = {"web_form", "whatsapp"}  # cualquier otro canal se guarda como "other"


class LeadValidationError(ValueError):
    """Lead inválido. El mensaje viaja tal cual al llamador (HTTP 422)."""


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #

def _fold(text: str) -> str:
    """Minúsculas y sin tildes: 'Próximo mes' -> 'proximo mes'."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _clean(value: Any, max_len: int = 200) -> Optional[str]:
    """Colapsa espacios, recorta y convierte '' en None."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:max_len] or None


# --------------------------------------------------------------------------- #
# Teléfono
# --------------------------------------------------------------------------- #

def normalize_phone(raw: Any, default_cc: str = "") -> Optional[str]:
    """
    Devuelve el teléfono en formato E.164 ('+51987654321') o None.

    Acepta el prefijo 'whatsapp:' que usan Twilio y similares. Sin '+' ni '00'
    hace falta default_cc (ej. '51'); si no, no se adivina el país.
    """
    text = _clean(raw)
    if not text:
        return None
    text = text.lower().removeprefix("whatsapp:").strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif text.startswith("00"):
        digits = digits[2:]
    elif default_cc:
        digits = default_cc + digits.lstrip("0")
    else:
        return None
    return f"+{digits}" if 8 <= len(digits) <= 15 else None


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #

_NUM = r"(\d[\d.,]*)"
_MULT = r"(k\b|mil\b|millon(?:es)?\b)"
_MULTIPLIER = {"k": 1_000, "mil": 1_000, "millon": 1_000_000, "millones": 1_000_000}
_RANGE_RE = re.compile(
    rf"{_NUM}\s*{_MULT}?\s*(?:-|–|\ba\b|\by\b|hasta)\s*(?:us\$|s/|\$|€)?\s*{_NUM}\s*{_MULT}?"
)
_SINGLE_RE = re.compile(rf"{_NUM}\s*{_MULT}?")
_CURRENCY_HINTS = [  # sin pista de moneda se asume USD ('$', 'US$', 'dólares')
    ("PEN", re.compile(r"(?<![a-z])s/|\bsoles?\b|\bpen\b")),
    ("EUR", re.compile(r"€|\beuros?\b|\beur\b")),
    ("MXN", re.compile(r"\bmxn\b|pesos? mexicanos?")),
    ("COP", re.compile(r"\bcop\b|pesos? colombianos?")),
]


def _to_float(num: str) -> Optional[float]:
    """'5,000' / '5.000' -> 5000; '5,5' -> 5.5; '1.250,75' -> 1250.75."""
    num = num.rstrip(".,")
    if "," in num and "." in num:
        # El último separador que aparece es el decimal.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num or "." in num:
        sep = "," if "," in num else "."
        groups = num.split(sep)
        if all(len(g) == 3 for g in groups[1:]):
            num = "".join(groups)        # separador de miles
        else:
            num = num.replace(",", ".")  # separador decimal
    try:
        return float(num)
    except ValueError:
        return None


def parse_budget_usd(raw: Any) -> Optional[float]:
    """
    'USD 5,000', '5k', 'S/ 12 mil', 8000 -> monto en USD. None si no hay monto.

    Los números sueltos se asumen en USD. En un rango ('2,000 - 5,000', '3-5k')
    devuelve la cota inferior. No interpreta 'menos de' ni 'más de'; eso lo lee el LLM.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None

    text = _fold(str(raw))
    match = _RANGE_RE.search(text)
    if match:
        low, m_low, high, m_high = match.groups()
        low_val, high_val = _to_float(low), _to_float(high)
        # '3-5k': el multiplicador del extremo alto aplica al bajo si este es menor.
        mult = m_low or (m_high if None not in (low_val, high_val) and low_val < high_val else None)
        amount = low_val
    else:
        match = _SINGLE_RE.search(text)
        if not match:
            return None
        amount, mult = _to_float(match.group(1)), match.group(2)

    if not amount or amount <= 0:
        return None
    amount *= _MULTIPLIER.get(mult, 1)
    currency = next((code for code, pat in _CURRENCY_HINTS if pat.search(text)), "USD")
    return round(amount * FX_TO_USD[currency], 2)


# --------------------------------------------------------------------------- #
# Urgencia
# --------------------------------------------------------------------------- #

_URGENCY_PATTERNS = [
    # El orden importa: "no es urgente" tiene que caer en exploring antes que en immediate.
    ("exploring", re.compile(
        r"sin (prisa|apuro|urgencia)|no (es )?urgente|explorando|evaluando|"
        r"solo (mirando|averiguando|cotizando)|a futuro|mas adelante")),
    ("immediate", re.compile(
        r"urgente|\basap\b|inmediat|\bhoy\b|esta semana|lo antes posible|cuanto antes")),
    ("this_month", re.compile(
        r"este mes|proximas semanas|\b(15|30) dias|treinta dias|un mes\b|\b1 mes\b|\b(2|dos) semanas")),
    ("this_quarter", re.compile(
        r"trimestre|\b(3|tres) meses|proximos meses|\b(60|90) dias")),
]


def parse_urgency(text: Optional[str]) -> str:
    """Devuelve immediate | this_month | this_quarter | exploring | unknown."""
    if not text:
        return "unknown"
    folded = _fold(text)
    return next((label for label, pat in _URGENCY_PATTERNS if pat.search(folded)), "unknown")


# --------------------------------------------------------------------------- #
# Normalización del lead
# --------------------------------------------------------------------------- #

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def normalize_lead(raw: dict, default_cc: str = "") -> dict:
    """Limpia y valida los campos. Lanza LeadValidationError si no hay un email válido."""
    email = (_clean(raw.get("email")) or "").lower()
    if not _EMAIL_RE.match(email):
        raise LeadValidationError("email inválido o ausente")

    first, _, last = (_clean(raw.get("name")) or "").partition(" ")
    source = re.sub(r"[\s-]+", "_", _fold(_clean(raw.get("source")) or "web_form"))
    budget = raw.get("budget")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool):
        budget = _clean(budget, 100)

    return {
        "email": email,
        "firstname": first or None,
        "lastname": last or None,
        "phone": normalize_phone(raw.get("phone"), default_cc),
        "company": _clean(raw.get("company")),
        "source": source if source in VALID_SOURCES else "other",
        "budget_raw": budget,
        "urgency_raw": _clean(raw.get("urgency"), 100),
        "message": _clean(raw.get("message"), 2000),
    }


# --------------------------------------------------------------------------- #
# Señales, score y tier
# --------------------------------------------------------------------------- #

def _positive(value: Any) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return float(value) if ok else None


def resolve_signals(lead: dict, llm: Optional[dict] = None) -> dict:
    """
    Une presupuesto y urgencia. Prioridad: campo estructurado > LLM > reglas sobre el mensaje.
    `llm` es el dict que devuelve la herramienta de extracción (puede venir incompleto o sucio).
    """
    budget = parse_budget_usd(lead.get("budget_raw"))
    urgency = parse_urgency(lead.get("urgency_raw"))

    if llm:
        amount = _positive(llm.get("budget_amount"))
        rate = FX_TO_USD.get(str(llm.get("budget_currency", "USD")).upper())
        if budget is None and amount and rate:
            budget = round(amount * rate, 2)
        if urgency == "unknown" and llm.get("urgency") in URGENCY_POINTS:
            urgency = llm["urgency"]

    if urgency == "unknown":
        urgency = parse_urgency(lead.get("message"))
    return {"budget_usd": budget, "urgency": urgency}


def needs_llm(lead: dict) -> bool:
    """Solo vale la pena llamar al modelo si hay texto libre o faltan señales."""
    signals = resolve_signals(lead)
    return bool(lead["message"]) or signals["budget_usd"] is None or signals["urgency"] == "unknown"


def budget_points(budget_usd: Optional[float]) -> int:
    if budget_usd is None:
        return BUDGET_UNKNOWN_POINTS
    return next((pts for floor, pts in BUDGET_BANDS if budget_usd >= floor), 0)


def score_lead(budget_usd: Optional[float], urgency: str) -> dict:
    """score = puntos de presupuesto + puntos de urgencia (0-100). Umbrales en HOT_MIN / WARM_MIN."""
    b = budget_points(budget_usd)
    u = URGENCY_POINTS.get(urgency, URGENCY_POINTS["unknown"])
    score = b + u
    tier = "HOT" if score >= HOT_MIN else "WARM" if score >= WARM_MIN else "COLD"
    return {"score": score, "tier": tier, "breakdown": {"budget": b, "urgency": u}}


def build_tags(tier: str, source: str, signals: dict) -> list[str]:
    """Etiquetas para la propiedad multicheckbox lead_tags de HubSpot."""
    tags = [tier.lower(), source]
    if signals["budget_usd"] is not None and signals["budget_usd"] >= HIGH_BUDGET_USD:
        tags.append("high_budget")
    if signals["urgency"] == "immediate":
        tags.append("urgent")
    return tags


# --------------------------------------------------------------------------- #
# Resumen y payload para HubSpot
# --------------------------------------------------------------------------- #

def clean_summary(text: Any, max_len: int = 200) -> Optional[str]:
    """
    Texto del LLM que termina en Slack y HubSpot. Se quitan '<' y '>' porque en Slack
    '<!channel>' dispara una mención a todo el canal.
    """
    return _clean(re.sub(r"[<>`]", "", str(text)), max_len) if text else None


URGENCY_ES = {
    "immediate": "inmediata", "this_month": "este mes", "this_quarter": "este trimestre",
    "exploring": "solo explorando", "unknown": "sin dato",
}


def fallback_summary(lead: dict, signals: dict) -> str:
    """Resumen determinista para cuando no hay LLM."""
    who = lead["company"] or " ".join(filter(None, [lead["firstname"], lead["lastname"]])) or lead["email"]
    budget = f"presupuesto ~USD {signals['budget_usd']:,.0f}" if signals["budget_usd"] else "sin presupuesto declarado"
    return f"{who}: {budget}, urgencia {URGENCY_ES[signals['urgency']]}."


def build_hubspot_body(lead: dict, signals: dict, result: dict, tags: list[str], summary: str) -> str:
    """
    Cuerpo JSON (como texto) para POST /crm/v3/objects/contacts/batch/upsert.
    Los nombres de propiedad son los internos de HubSpot. Los None se omiten.
    """
    props = {
        "firstname": lead["firstname"],
        "lastname": lead["lastname"],
        "phone": lead["phone"],
        "company": lead["company"],
        "lead_tier": result["tier"],
        "lead_score": result["score"],
        "lead_budget_usd": signals["budget_usd"],
        "lead_urgency": signals["urgency"],
        "lead_tags": ";".join(tags),  # multicheckbox: valores separados por punto y coma
        "lead_ai_summary": summary,
    }
    props = {k: v for k, v in props.items() if v is not None}
    payload = {"inputs": [{"idProperty": "email", "id": lead["email"], "properties": props}]}
    return json.dumps(payload, ensure_ascii=False)


def qualify(lead: dict, llm: Optional[dict] = None) -> dict:
    """Lead normalizado (+ señales del LLM, si hubo) -> respuesta completa del servicio."""
    signals = resolve_signals(lead, llm)
    result = score_lead(signals["budget_usd"], signals["urgency"])
    tags = build_tags(result["tier"], lead["source"], signals)
    summary = clean_summary((llm or {}).get("summary")) or fallback_summary(lead, signals)
    return {
        "tier": result["tier"],
        "score": result["score"],
        "breakdown": result["breakdown"],
        "signals": signals,
        "tags": tags,
        "summary": summary,
        "method": "llm+rules" if llm else "rules",
        "lead": {k: lead[k] for k in ("email", "firstname", "lastname", "phone", "company", "source")},
        "hubspot_body": build_hubspot_body(lead, signals, result, tags, summary),
    }
