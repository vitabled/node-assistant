"""Models for the «Хостинги» catalogue (Wave-4 Plan A)."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class NoteField(BaseModel):
    """One free-form note on a hosting card: a topic plus its text. A list of
    these rather than one big `notes` blob because the user files remarks by
    subject (оплата, поддержка, ограничения) and wants them separately."""
    topic: str = ""
    text: str = ""

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        # Single-line label: collapse any whitespace run, same as a tag.
        return " ".join((v or "").split())[:80]

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        # CR/LF are kept on purpose — unlike a tag, this is a multi-line note.
        return (v or "").strip()[:5000]


class Tariff(BaseModel):
    name: str = ""
    specs: str = ""          # free-text spec summary (CPU/RAM/disk/…)
    # Network channel width. Free text, not a number: providers quote a port
    # speed, a guarantee and a traffic cap together ("1 Гбит/с, 20 ТБ",
    # "10G unmetered") — there is no single numeric semantic to store.
    bandwidth: str = ""
    price: float = Field(default=0, ge=0)
    currency: str = "USD"
    period: str = "mo"       # mo | yr | hr | once (free-text label)
    # Per-tariff remark. No topic here: a tariff already is the subject.
    note: str = ""

    @field_validator("note")
    @classmethod
    def _note(cls, v: str) -> str:
        return (v or "").strip()[:2000]


class BsSubnet(BaseModel):
    """Строка таблицы «БС подсети»: подсеть, её ASN/владелец и результат
    последней проверки. Все поля — свободный текст: сюда переносят выписки из
    чужих источников («AS12345», «2026-07-01», «отвечает, 20 ms»), и приводить
    их к типам значило бы терять то, что человек записал."""
    network: str = ""      # сама подсеть, 10.0.0.0/24
    asn: str = ""
    org: str = ""          # организация-владелец
    checked_at: str = ""   # дата проверки
    response: str = ""     # отклик

    @field_validator("network", "asn", "org", "checked_at", "response")
    @classmethod
    def _cell(cls, v: str) -> str:
        # Ячейка таблицы — однострочная: схлопываем любые пробелы, как в теге.
        return " ".join((v or "").split())[:120]


class Location(BaseModel):
    city: str = ""
    country_code: str = Field(default="", max_length=2)
    lat: float = Field(default=0, ge=-90, le=90)
    lng: float = Field(default=0, ge=-180, le=180)
    note: str = ""


class AsnRef(BaseModel):
    """An autonomous system a hosting operates on (Wave-8 §6). Structured rather
    than a bare string because §7 «Анализ подписки» fills name/website from
    RDAP/PeeringDB — the number alone would lose that."""
    number: int = Field(default=0, ge=0)
    name: str = ""
    website: str = ""


class HostingMetrics(BaseModel):
    """Subjective 1..100 scores for a hosting. Every score is Optional on purpose:
    `None` means «не оценено» and must stay distinguishable from a low score."""
    price: Optional[float] = None
    quality: Optional[float] = None
    loyalty: Optional[float] = None
    fairuse: Optional[float] = None
    panel: Optional[float] = None       # удобство панели
    ru_access: Optional[float] = None   # доступность в РФ
    # Fair-use policy is not a thing at every provider — the card hides the row
    # rather than showing a metric that will never be scored.
    fairuse_hidden: bool = False

    @field_validator("price", "quality", "loyalty", "fairuse", "panel", "ru_access")
    @classmethod
    def _score(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        # Chained comparison rather than two `or`-ed checks: NaN and ±inf fail it
        # too (NaN compares False against everything).
        if not 1.0 <= v <= 100.0:
            raise ValueError("Оценка должна быть числом от 1.0 до 100.0")
        return round(v, 1)


class HostingBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    website: str = ""
    notes: str = ""
    features: str = ""
    # Account-level free-form tags (Wave-8 §1). Normalised: trimmed, CR/LF-free,
    # ≤24 chars each, deduped, ≤10 per hosting.
    tags: list[str] = Field(default_factory=list)
    # Ids in the shared media store (services/media_store.py) — screenshots of the
    # panel, a price list, a network map. Only ids: the bytes live in one place.
    media: list[str] = Field(default_factory=list)
    tariffs: list[Tariff] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    asns: list[AsnRef] = Field(default_factory=list)
    # Records written before this field existed have no `metrics` key (the store
    # returns raw JSON), so it must default instead of being required.
    metrics: HostingMetrics = Field(default_factory=HostingMetrics)
    # Free-form notes, each with its own topic. Legacy `notes` (a single blob)
    # stays as-is — old cards keep rendering without a migration.
    note_fields: list[NoteField] = Field(default_factory=list)
    # Таблица «БС подсети» — учёт проверенных подсетей провайдера.
    bs_subnets: list[BsSubnet] = Field(default_factory=list)
    # Whether the provider has a management API at all. Tri-state: None means
    # «неизвестно» and must stay distinguishable from an explicit «нет».
    has_api: Optional[bool] = None
    # Optional link to an infra-billing provider (kept loose — independent module).
    provider_ref: Optional[str] = None

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v or []:
            # split() collapses any whitespace run (incl. CR/LF) and trims.
            t = " ".join((raw or "").split())[:24]
            if t and t not in out:
                out.append(t)
            if len(out) >= 10:
                break
        return out

    @field_validator("bs_subnets")
    @classmethod
    def _prune_bs_subnets(cls, v: list[BsSubnet]) -> list[BsSubnet]:
        # Пустая строка остаётся от нетронутого «Добавить» — не копим её.
        return [r for r in (v or [])
                if r.network or r.asn or r.org or r.checked_at or r.response][:200]

    @field_validator("note_fields")
    @classmethod
    def _prune_note_fields(cls, v: list[NoteField]) -> list[NoteField]:
        # A row with neither topic nor text is what an untouched «добавить поле»
        # leaves behind — drop it so the form can't accumulate blanks. The 30-cap
        # trims silently, like tags.
        return [n for n in (v or []) if n.topic or n.text][:30]
