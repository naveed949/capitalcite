"""In-process graph. Tests and eval --suite graph use this. No Bolt."""

from __future__ import annotations

from collections import defaultdict, deque

from capitalcite.kg.types import (
    Bank,
    FilingNode,
    GraphHit,
    GraphQuery,
    GraphQueryKind,
    GraphResult,
    GraphSnapshot,
    LoadReceipt,
    MetricNode,
    render_hits,
)


def _year(period: str) -> str:
    return period[:4] if len(period) >= 4 and period[:4].isdigit() else period


def _metric_key(metric: MetricNode) -> tuple[str, str, str]:
    scope = metric.ticker.strip().upper() or metric.accession
    return (scope, metric.name.strip().lower(), metric.period)


class MemoryGraphStore:
    """MERGE by RSSD, accession, and metric key. A second load does not duplicate."""

    def __init__(self) -> None:
        self._banks: dict[str, Bank] = {}
        self._edges: set[tuple[str, str]] = set()
        self._filings: dict[str, FilingNode] = {}
        self._metrics: dict[tuple[str, str, str], MetricNode] = {}
        self._ticker: dict[str, str] = {}
        self._children: dict[str, tuple[str, ...]] = {}
        self._parents: dict[str, tuple[str, ...]] = {}

    async def load(self, snapshot: GraphSnapshot) -> LoadReceipt:
        for bank in snapshot.banks:
            self._banks[bank.rssd] = bank
        for rel in snapshot.parents:
            if rel.parent_rssd in self._banks and rel.child_rssd in self._banks:
                self._edges.add((rel.parent_rssd, rel.child_rssd))
        for filing in snapshot.filings:
            self._filings[filing.accession] = filing
        for metric in snapshot.metrics:
            self._metrics[_metric_key(metric)] = metric
        self._reindex()
        return LoadReceipt(
            backend="memory",
            banks=len(self._banks),
            filings=len(self._filings),
            metrics=len(self._metrics),
            edges=len(self._edges),
        )

    def _reindex(self) -> None:
        self._ticker = {
            bank.ticker.strip().upper(): bank.rssd
            for bank in self._banks.values()
            if bank.ticker.strip()
        }
        children: dict[str, list[str]] = defaultdict(list)
        parents: dict[str, list[str]] = defaultdict(list)
        for parent, child in sorted(self._edges):
            children[parent].append(child)
            parents[child].append(parent)
        self._children = {key: tuple(values) for key, values in children.items()}
        self._parents = {key: tuple(values) for key, values in parents.items()}

    async def query(self, query: GraphQuery) -> GraphResult:
        if query.kind is GraphQueryKind.PATH:
            return self._path(query)
        bank = self._resolve(query.ticker, query.rssd)
        if bank is None:
            return GraphResult(ok=False, detail="unknown bank")
        if query.kind is GraphQueryKind.SUBSIDIARIES:
            return self._subsidiaries(bank, query.depth)
        if query.kind is GraphQueryKind.PARENT:
            return self._parent(bank)
        if query.kind is GraphQueryKind.FILINGS:
            return self._filings_of(bank, query.form, query.period)
        return self._metric(bank, query.name or "", query.period)

    async def close(self) -> None:
        return None

    def _resolve(self, ticker: str | None, rssd: str | None) -> Bank | None:
        if rssd and rssd.strip():
            found = self._banks.get(rssd.strip())
            if found is not None:
                return found
        if ticker and ticker.strip():
            key = self._ticker.get(ticker.strip().upper())
            if key is not None:
                return self._banks.get(key)
        return None

    def _subsidiaries(self, bank: Bank, depth: int) -> GraphResult:
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(bank.rssd, 0)])
        hits: list[GraphHit] = []
        while queue:
            current, hops = queue.popleft()
            if hops >= depth:
                continue
            for child in self._children.get(current, ()):
                if child in seen:
                    continue
                seen.add(child)
                node = self._banks[child]
                hits.append(_bank_hit(node))
                queue.append((child, hops + 1))
        if not hits:
            return GraphResult(ok=False, detail=f"no subsidiaries for {bank.rssd}")
        return GraphResult(ok=True, detail=render_hits(hits), hits=tuple(hits))

    def _parent(self, bank: Bank) -> GraphResult:
        hits = tuple(_bank_hit(self._banks[rssd]) for rssd in self._parents.get(bank.rssd, ()))
        if not hits:
            return GraphResult(ok=False, detail=f"no parent for {bank.rssd}")
        return GraphResult(ok=True, detail=render_hits(hits), hits=hits)

    def _filings_of(self, bank: Bank, form: str | None, period: str | None) -> GraphResult:
        wanted_form = form.strip().upper() if form and form.strip() else None
        wanted_year = _year(period.strip()) if period and period.strip() else None
        hits: list[GraphHit] = []
        for filing in sorted(self._filings.values(), key=lambda item: item.accession):
            if filing.ticker.upper() != bank.ticker.upper() and filing.cik != bank.cik:
                continue
            if wanted_form is not None and filing.form.upper() != wanted_form:
                continue
            if wanted_year is not None and _year(filing.period) != wanted_year:
                continue
            hits.append(
                GraphHit(
                    label="Filing",
                    fields={
                        "accession": filing.accession,
                        "ticker": filing.ticker,
                        "form": filing.form,
                        "period": filing.period,
                    },
                )
            )
        if not hits:
            return GraphResult(ok=False, detail=f"no filings for {bank.ticker or bank.rssd}")
        return GraphResult(ok=True, detail=render_hits(hits), hits=tuple(hits))

    def _metric(self, bank: Bank, name: str, period: str | None) -> GraphResult:
        needle = name.strip().lower()
        wanted_year = _year(period.strip()) if period and period.strip() else None
        hits: list[GraphHit] = []
        ranked = sorted(
            self._metrics.values(),
            key=lambda item: (item.ticker, item.name, item.period),
        )
        for metric in ranked:
            if metric.ticker.upper() != bank.ticker.upper():
                continue
            if needle not in metric.name.lower():
                continue
            if wanted_year is not None and _year(metric.period) != wanted_year:
                continue
            hits.append(
                GraphHit(
                    label="Metric",
                    fields={
                        "name": metric.name,
                        "period": metric.period,
                        "value": metric.value,
                        "unit": metric.unit,
                        "ticker": metric.ticker,
                    },
                )
            )
        if not hits:
            return GraphResult(ok=False, detail=f"no metric {name} for {bank.ticker or bank.rssd}")
        return GraphResult(ok=True, detail=render_hits(hits), hits=tuple(hits))

    def _path(self, query: GraphQuery) -> GraphResult:
        start = self._resolve(query.ticker, None)
        end = self._resolve(None, query.rssd)
        if start is None or end is None:
            return GraphResult(ok=False, detail="unknown bank")
        if start.rssd == end.rssd:
            hit = _bank_hit(start)
            return GraphResult(ok=True, detail=render_hits([hit]), hits=(hit,))
        neighbors: dict[str, tuple[str, ...]] = {}
        for rssd in self._banks:
            linked = tuple(
                dict.fromkeys((*self._children.get(rssd, ()), *self._parents.get(rssd, ())))
            )
            neighbors[rssd] = linked
        prev: dict[str, str | None] = {start.rssd: None}
        queue: deque[str] = deque([start.rssd])
        found = False
        while queue:
            current = queue.popleft()
            if current == end.rssd:
                found = True
                break
            for nxt in neighbors.get(current, ()):
                if nxt in prev:
                    continue
                prev[nxt] = current
                queue.append(nxt)
        if not found:
            return GraphResult(ok=False, detail="no path")
        chain: list[str] = []
        cursor: str | None = end.rssd
        while cursor is not None:
            chain.append(cursor)
            cursor = prev[cursor]
        chain.reverse()
        hits = tuple(_bank_hit(self._banks[rssd]) for rssd in chain)
        return GraphResult(ok=True, detail=render_hits(hits), hits=hits)


def _bank_hit(bank: Bank) -> GraphHit:
    fields = {"rssd": bank.rssd, "name": bank.name}
    if bank.ticker:
        fields["ticker"] = bank.ticker
    if bank.cik:
        fields["cik"] = bank.cik
    if bank.is_bhc:
        fields["is_bhc"] = "true"
    return GraphHit(label="Bank", fields=fields)
