"""Source ledger — the set of URLs a run is allowed to retrieve (PM1.B1).

Retrieval is search-result-gated. A run may fetch a URL only if a `web_search`
call in that same run already returned it, or if an operator declared it as a
seed in the typed pack input and the active source policy admits its domain.
That is what stops a page from steering the next fetch: a link inside a
retrieved document is text, and text does not enter this ledger.

The ledger is a JSON file under `runs/<id>/content/source-ledger.json` rather
than process state, so a resumed run keeps its gate and an operator can read
exactly which URLs were reachable and why.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from product_factory.connectors.errors import ConnectorPolicyDenied

if TYPE_CHECKING:
    from product_factory.policy.source_policy import SourcePolicyProfile

LEDGER_FILENAME = "source-ledger.json"
LEDGER_SCHEMA_ID = "source_ledger.v1"

ORIGIN_SEARCH = "web_search"
ORIGIN_SEED = "operator_seed"

# Only https URLs are admissible; the URL policy would reject anything else at
# fetch time anyway, and an inadmissible entry in the ledger is just noise.
_ADMISSIBLE_SCHEMES = frozenset({"https"})
_DEFAULT_PORTS = {"https": 443, "http": 80}

# Connector tools whose results establish retrieval permission.
SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"web_search"})


class SourceNotInLedger(ConnectorPolicyDenied):
    """A fetch was attempted for a URL no search result or seed admitted."""

    exit_code = 8

    @property
    def denial_code(self) -> str:
        return "source_not_in_ledger"


def canonical_url(url: str) -> str:
    """Canonical form used as the ledger key.

    Casing, a default port, a fragment, and an empty path are all presentation;
    the query string is not, because it usually selects the document. Any
    embedded credential is dropped rather than normalized — see `url_policy`,
    which refuses such URLs outright.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not scheme or not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


@dataclass(frozen=True)
class SourceLedgerEntry:
    """One admitted URL and the evidence that admitted it."""

    url: str
    host: str
    origin: str
    recorded_at: str
    task_id: str = ""
    tool_call_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "origin": self.origin,
            "recorded_at": self.recorded_at,
            "task_id": self.task_id,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SourceLedgerEntry | None:
        url = canonical_url(str(payload.get("url") or ""))
        if not url:
            return None
        return cls(
            url=url,
            host=str(payload.get("host") or urlsplit(url).hostname or ""),
            origin=str(payload.get("origin") or ORIGIN_SEARCH),
            recorded_at=str(payload.get("recorded_at") or ""),
            task_id=str(payload.get("task_id") or ""),
            tool_call_id=str(payload.get("tool_call_id") or ""),
        )


class SourceLedger:
    """File-backed record of the URLs one run may retrieve."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_run(cls, run_dir: Path) -> SourceLedger:
        return cls(Path(run_dir) / "content" / LEDGER_FILENAME)

    def entries(self) -> tuple[SourceLedgerEntry, ...]:
        return tuple(self._load().values())

    def urls(self) -> tuple[str, ...]:
        return tuple(self._load())

    def __len__(self) -> int:
        return len(self._load())

    def entry_for(self, url: str) -> SourceLedgerEntry | None:
        key = canonical_url(url)
        if not key:
            return None
        return self._load().get(key)

    def is_allowed(self, url: str) -> bool:
        return self.entry_for(url) is not None

    def assert_allowed(
        self,
        url: str,
        *,
        connector_id: str = "",
        tool_name: str = "",
    ) -> SourceLedgerEntry:
        """Return the admitting entry, or raise `SourceNotInLedger`."""
        entry = self.entry_for(url)
        if entry is None:
            raise SourceNotInLedger(
                "URL was not returned by a search in this run and is not a declared seed",
                connector_id=connector_id,
                tool_name=tool_name,
                details={
                    "url": str(url or "").strip(),
                    "canonical_url": canonical_url(url),
                    "ledger_path": str(self.path),
                    "ledger_size": len(self._load()),
                },
            )
        return entry

    def record_search_results(
        self,
        urls: Iterable[str],
        *,
        task_id: str = "",
        tool_call_id: str = "",
        origin: str = ORIGIN_SEARCH,
    ) -> tuple[str, ...]:
        """Admit the URLs a search returned; return the newly admitted ones.

        Re-recording an existing URL keeps the first admission, so the ledger
        shows which call first made a source reachable.
        """
        return self._record(
            urls,
            origin=origin,
            task_id=task_id,
            tool_call_id=tool_call_id,
            allows_host=None,
        )

    def record_seed_urls(
        self,
        urls: Iterable[str],
        *,
        policy: SourcePolicyProfile | None,
        task_id: str = "",
    ) -> tuple[str, ...]:
        """Admit operator-declared seed URLs that the source policy allows.

        With no resolved source policy nothing is admitted: a seed list is an
        operator assertion, and without a policy there is nothing to check it
        against.
        """
        if policy is None:
            return ()
        return self._record(
            urls,
            origin=ORIGIN_SEED,
            task_id=task_id,
            tool_call_id="",
            allows_host=policy.allows_domain,
        )

    def _record(
        self,
        urls: Iterable[str],
        *,
        origin: str,
        task_id: str,
        tool_call_id: str,
        allows_host: Callable[[str], bool] | None,
    ) -> tuple[str, ...]:
        known = self._load()
        recorded_at = datetime.now(UTC).isoformat()
        added: list[str] = []
        for raw in urls or ():
            key = canonical_url(str(raw or ""))
            if not key or key in known:
                continue
            parsed = urlsplit(key)
            if parsed.scheme not in _ADMISSIBLE_SCHEMES:
                continue
            host = parsed.hostname or ""
            if allows_host is not None and not allows_host(host):
                continue
            known[key] = SourceLedgerEntry(
                url=key,
                host=host,
                origin=origin,
                recorded_at=recorded_at,
                task_id=task_id,
                tool_call_id=tool_call_id,
            )
            added.append(key)
        if added:
            self._save(known)
        return tuple(added)

    def _load(self) -> dict[str, SourceLedgerEntry]:
        """Read the file every time: tasks share a run directory."""
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries: dict[str, SourceLedgerEntry] = {}
        for item in payload.get("entries") or []:
            if not isinstance(item, dict):
                continue
            entry = SourceLedgerEntry.from_payload(item)
            if entry is not None:
                entries[entry.url] = entry
        return entries

    def _save(self, entries: dict[str, SourceLedgerEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_id": LEDGER_SCHEMA_ID,
            "entries": [entries[key].as_payload() for key in sorted(entries)],
        }
        # Replace atomically so a crash mid-write cannot leave a ledger that
        # parses as empty — an empty ledger denies every fetch, but a truncated
        # one would also lose the audit trail of what was reachable.
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def urls_from_provenance(provenance: Iterable[Any]) -> tuple[str, ...]:
    """Pull URL sources out of a connector result's provenance list."""
    urls: list[str] = []
    for item in provenance or ():
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "url") != "url":
            continue
        source = str(item.get("source") or "").strip()
        if source and source not in urls:
            urls.append(source)
    return tuple(urls)


__all__ = [
    "LEDGER_FILENAME",
    "LEDGER_SCHEMA_ID",
    "ORIGIN_SEARCH",
    "ORIGIN_SEED",
    "SEARCH_TOOL_NAMES",
    "SourceLedger",
    "SourceLedgerEntry",
    "SourceNotInLedger",
    "canonical_url",
    "urls_from_provenance",
]
