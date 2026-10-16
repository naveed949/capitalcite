from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GraphQueryKind(StrEnum):
    SUBSIDIARIES = "subsidiaries"
    PARENT = "parent"
    FILINGS = "filings"
    METRIC = "metric"
    PATH = "path"


class Bank(BaseModel):
    model_config = ConfigDict(frozen=True)
    rssd: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ticker: str = ""
    cik: str = ""
    lei: str = ""
    fdic_cert: str = ""
    is_bhc: bool = False


class FilingNode(BaseModel):
    model_config = ConfigDict(frozen=True)
    accession: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    cik: str = ""
    form: str = Field(min_length=1)
    period: str = Field(min_length=1)
    source_url: str = ""


class MetricNode(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str = Field(min_length=1)
    period: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: str = ""
    accession: str = ""
    ticker: str = Field(min_length=1)


class ParentOf(BaseModel):
    model_config = ConfigDict(frozen=True)
    parent_rssd: str = Field(min_length=1)
    child_rssd: str = Field(min_length=1)


class GraphSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    banks: tuple[Bank, ...]
    parents: tuple[ParentOf, ...] = ()
    filings: tuple[FilingNode, ...] = ()
    metrics: tuple[MetricNode, ...] = ()


class GraphQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: GraphQueryKind
    ticker: str | None = None
    rssd: str | None = None
    name: str | None = None
    period: str | None = None
    form: str | None = None
    depth: int = Field(default=1, ge=1, le=4)

    @field_validator("depth", mode="before")
    @classmethod
    def null_depth_is_default(cls, value: object) -> object:
        return 1 if value is None else value

    @model_validator(mode="after")
    def needs_an_anchor(self) -> GraphQuery:
        if not (self.ticker and self.ticker.strip()) and not (self.rssd and self.rssd.strip()):
            raise ValueError("graph_query needs ticker or rssd")
        if self.kind is GraphQueryKind.METRIC and not (self.name and self.name.strip()):
            raise ValueError("metric query needs name")
        if self.kind is GraphQueryKind.PATH and not (
            (self.ticker and self.ticker.strip()) and (self.rssd and self.rssd.strip())
        ):
            raise ValueError("path query needs ticker and rssd as the two ends")
        return self


class GraphHit(BaseModel):
    model_config = ConfigDict(frozen=True)
    label: str
    fields: dict[str, str]


class GraphResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    detail: str
    hits: tuple[GraphHit, ...] = ()


class LoadReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    backend: str
    banks: int = Field(ge=0)
    filings: int = Field(ge=0)
    metrics: int = Field(ge=0)
    edges: int = Field(ge=0)


class GraphStore(Protocol):
    async def load(self, snapshot: GraphSnapshot) -> LoadReceipt: ...

    async def query(self, query: GraphQuery) -> GraphResult: ...

    async def close(self) -> None: ...


def render_hits(hits: list[GraphHit] | tuple[GraphHit, ...]) -> str:
    parts: list[str] = []
    for hit in hits:
        body = " ".join(f"{key}={value}" for key, value in hit.fields.items() if value)
        parts.append(f"{hit.label} {body}".strip())
    return "; ".join(parts)
