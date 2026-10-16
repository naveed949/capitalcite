from __future__ import annotations

from typing import Any

from capitalcite.kg.types import (
    GraphHit,
    GraphQuery,
    GraphQueryKind,
    GraphResult,
    GraphSnapshot,
    LoadReceipt,
    render_hits,
)


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str, *, driver: Any = None) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: Any = driver
        self._owns_driver = driver is None

    def _driver_or_raise(self) -> Any:
        if self._driver is None:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(self._uri, auth=(self._user, self._password))
        return self._driver

    async def load(self, snapshot: GraphSnapshot) -> LoadReceipt:
        driver = self._driver_or_raise()
        async with driver.session() as session:
            for bank in snapshot.banks:
                await session.run(
                    "MERGE (b:Bank {rssd: $rssd}) "
                    "SET b.name = $name, b.ticker = $ticker, b.cik = $cik, "
                    "b.lei = $lei, b.fdic_cert = $fdic_cert, b.is_bhc = $is_bhc",
                    rssd=bank.rssd,
                    name=bank.name,
                    ticker=bank.ticker,
                    cik=bank.cik,
                    lei=bank.lei,
                    fdic_cert=bank.fdic_cert,
                    is_bhc=bank.is_bhc,
                )
            for rel in snapshot.parents:
                await session.run(
                    "MATCH (p:Bank {rssd: $parent}) "
                    "MATCH (c:Bank {rssd: $child}) "
                    "MERGE (p)-[:PARENT_OF]->(c)",
                    parent=rel.parent_rssd,
                    child=rel.child_rssd,
                )
            for filing in snapshot.filings:
                await session.run(
                    "MERGE (f:Filing {accession: $accession}) "
                    "SET f.ticker = $ticker, f.cik = $cik, f.form = $form, "
                    "f.period = $period, f.source_url = $source_url "
                    "WITH f MATCH (b:Bank {ticker: $ticker}) "
                    "WHERE b.ticker <> '' "
                    "MERGE (b)-[:FILED]->(f)",
                    accession=filing.accession,
                    ticker=filing.ticker,
                    cik=filing.cik,
                    form=filing.form,
                    period=filing.period,
                    source_url=filing.source_url,
                )
            for metric in snapshot.metrics:
                await session.run(
                    "MERGE (m:Metric {ticker: $ticker, name: $name, period: $period}) "
                    "SET m.value = $value, m.unit = $unit, m.accession = $accession "
                    "WITH m MATCH (b:Bank {ticker: $ticker}) "
                    "WHERE b.ticker <> '' "
                    "MERGE (b)-[:REPORTS]->(m)",
                    ticker=metric.ticker,
                    name=metric.name,
                    period=metric.period,
                    value=metric.value,
                    unit=metric.unit,
                    accession=metric.accession,
                )
        return LoadReceipt(
            backend="neo4j",
            banks=len(snapshot.banks),
            filings=len(snapshot.filings),
            metrics=len(snapshot.metrics),
            edges=len({(rel.parent_rssd, rel.child_rssd) for rel in snapshot.parents}),
        )

    async def query(self, query: GraphQuery) -> GraphResult:
        driver = self._driver_or_raise()
        cypher, params = _cypher(query)
        async with driver.session() as session:
            result = await session.run(cypher, **params)
            records = [record async for record in result]
        found: list[GraphHit] = []
        for record in records:
            hit = _record_hit(record)
            if hit is not None:
                found.append(hit)
        hits = tuple(found)
        if not hits:
            return GraphResult(ok=False, detail="no graph matches")
        return GraphResult(ok=True, detail=render_hits(hits), hits=hits)

    async def close(self) -> None:
        if self._driver is not None and self._owns_driver:
            await self._driver.close()
        self._driver = None


def _cypher(query: GraphQuery) -> tuple[str, dict[str, object]]:
    ticker = (query.ticker or "").strip().upper()
    rssd = (query.rssd or "").strip()
    params: dict[str, object] = {
        "ticker": ticker,
        "rssd": rssd,
        "name": (query.name or "").strip().lower(),
        "form": (query.form or "").strip().upper(),
        "period": (query.period or "").strip()[:4],
        "depth": query.depth,
    }
    if query.kind is GraphQueryKind.SUBSIDIARIES:
        # Variable-length patterns reject parameters, and depth is already 1..4.
        depth = int(query.depth)
        return (
            "MATCH (b:Bank) WHERE ($rssd <> '' AND b.rssd = $rssd) "
            "OR ($ticker <> '' AND b.ticker = $ticker) "
            f"MATCH (b)-[:PARENT_OF*1..{depth}]->(c:Bank) "
            "RETURN DISTINCT c.name AS name, c.rssd AS rssd, c.ticker AS ticker, "
            "c.cik AS cik, 'Bank' AS label",
            params,
        )
    if query.kind is GraphQueryKind.PARENT:
        return (
            "MATCH (c:Bank) WHERE ($rssd <> '' AND c.rssd = $rssd) "
            "OR ($ticker <> '' AND c.ticker = $ticker) "
            "MATCH (p:Bank)-[:PARENT_OF]->(c) "
            "RETURN p.name AS name, p.rssd AS rssd, p.ticker AS ticker, p.cik AS cik, "
            "'Bank' AS label",
            params,
        )
    if query.kind is GraphQueryKind.FILINGS:
        return (
            "MATCH (b:Bank) WHERE ($rssd <> '' AND b.rssd = $rssd) "
            "OR ($ticker <> '' AND b.ticker = $ticker) "
            "MATCH (b)-[:FILED]->(f:Filing) "
            "WHERE ($form = '' OR f.form = $form) "
            "AND ($period = '' OR f.period STARTS WITH $period) "
            "RETURN f.accession AS accession, f.ticker AS ticker, f.form AS form, "
            "f.period AS period, 'Filing' AS label",
            params,
        )
    if query.kind is GraphQueryKind.METRIC:
        return (
            "MATCH (b:Bank) WHERE ($rssd <> '' AND b.rssd = $rssd) "
            "OR ($ticker <> '' AND b.ticker = $ticker) "
            "MATCH (b)-[:REPORTS]->(m:Metric) "
            "WHERE ($name = '' OR toLower(m.name) CONTAINS $name) "
            "AND ($period = '' OR m.period STARTS WITH $period) "
            "RETURN m.name AS name, m.period AS period, m.value AS value, "
            "m.unit AS unit, m.ticker AS ticker, 'Metric' AS label",
            params,
        )
    return (
        "MATCH (a:Bank) WHERE $ticker <> '' AND a.ticker = $ticker "
        "MATCH (b:Bank) WHERE $rssd <> '' AND b.rssd = $rssd "
        "MATCH p = shortestPath((a)-[:PARENT_OF*1..4]-(b)) "
        "UNWIND nodes(p) AS n "
        "RETURN n.name AS name, n.rssd AS rssd, n.ticker AS ticker, n.cik AS cik, "
        "'Bank' AS label",
        params,
    )


def _record_hit(record: Any) -> GraphHit | None:
    data = dict(record)
    label = str(data.pop("label", "Node"))
    fields = {key: str(value) for key, value in data.items() if value is not None and str(value)}
    if not fields:
        return None
    return GraphHit(label=label, fields=fields)
