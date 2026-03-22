"""CRM CSV Outcome Resolver.

Reads deals.csv and leads.csv from a configurable directory,
compares with a saved snapshot, and emits OutcomeEvents for stage transitions.

Configuration:
    Set OPENEXP_CRM_DIR environment variable to the CRM directory path.
    The directory should contain relationships/deals.csv and relationships/leads.csv.
"""
import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import DATA_DIR
from ..outcome import OutcomeEvent, OutcomeResolver

logger = logging.getLogger(__name__)

# Reward values for different outcome types
REWARD_TABLE = {
    "payment_received": 1.0,
    "deal_closed": 0.8,
    "client_yes": 0.6,
    "meaningful_response": 0.4,
    "deal_lost": -0.5,
}

# Stage transition → (event_name, reward)
DEAL_TRANSITIONS: Dict[Tuple[str, str], Tuple[str, float]] = {
    ("negotiation", "won"): ("deal_closed", REWARD_TABLE["deal_closed"]),
    ("negotiation", "closed"): ("deal_closed", REWARD_TABLE["deal_closed"]),
    ("delivered", "invoiced"): ("deal_closed", REWARD_TABLE["deal_closed"]),
    ("invoiced", "paid"): ("payment_received", REWARD_TABLE["payment_received"]),
    ("*", "lost"): ("deal_lost", REWARD_TABLE["deal_lost"]),
    ("*", "cancelled"): ("deal_lost", REWARD_TABLE["deal_lost"]),
}

LEAD_TRANSITIONS: Dict[Tuple[str, str], Tuple[str, float]] = {
    ("new", "qualified"): ("meaningful_response", REWARD_TABLE["meaningful_response"]),
    ("qualified", "proposal"): ("client_yes", REWARD_TABLE["client_yes"]),
    ("qualified", "negotiation"): ("client_yes", REWARD_TABLE["client_yes"]),
    ("proposal", "negotiation"): ("client_yes", REWARD_TABLE["client_yes"]),
    ("negotiation", "won"): ("deal_closed", REWARD_TABLE["deal_closed"]),
    ("negotiation", "closed"): ("deal_closed", REWARD_TABLE["deal_closed"]),
    ("*", "lost"): ("deal_lost", REWARD_TABLE["deal_lost"]),
    ("*", "dead"): ("deal_lost", REWARD_TABLE["deal_lost"]),
}


def _read_csv(path: Path) -> List[Dict]:
    """Read a CSV file into list of dicts. Returns [] if file doesn't exist."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _match_transition(
    old_stage: str,
    new_stage: str,
    table: Dict[Tuple[str, str], Tuple[str, float]],
) -> Optional[Tuple[str, float]]:
    """Match a stage transition to the reward table. Supports wildcard '*'."""
    key = (old_stage, new_stage)
    if key in table:
        return table[key]
    wildcard_key = ("*", new_stage)
    if wildcard_key in table:
        return table[wildcard_key]
    return None


def _extract_core(id_str: str) -> str:
    """Extract core identifier by stripping type prefix.

    'cli-dt-001' → 'dt-001', 'comp-squad' → 'squad', 'lead-squad-001' → 'squad-001'
    """
    parts = id_str.split("-", 1)
    if len(parts) == 2 and parts[0] in ("cli", "comp", "lead", "deal"):
        return parts[1]
    return id_str


def client_matches(pred_client: str, crm_client: str) -> bool:
    """Check if two client IDs match (exact or core match).

    Requires exact match or same core ID (prefix-stripped).
    Minimum 2 chars in core to avoid false positives.

    Examples:
        comp-squad == comp-squad (exact)
        cli-dt-001 matches comp-dt-001 (core: dt-001)
        comp-dt matches cli-dt (core: dt)
        comp-a-1 does NOT match cli-a-2 (cores: a-1 vs a-2)
    """
    if pred_client == crm_client:
        return True
    pred_core = _extract_core(pred_client)
    crm_core = _extract_core(crm_client)
    return (
        bool(pred_core)
        and bool(crm_core)
        and len(pred_core) >= 2
        and pred_core == crm_core
    )


class CRMCSVResolver(OutcomeResolver):
    """Detects CRM stage transitions by diffing CSV snapshots."""

    def __init__(self, crm_dir: Optional[Path] = None, snapshot_dir: Optional[Path] = None):
        from ..core.config import CRM_DIR
        self.crm_dir = Path(crm_dir) if crm_dir else CRM_DIR
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else DATA_DIR
        if self.snapshot_dir:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "crm_csv"

    def detect_outcomes(self) -> List[OutcomeEvent]:
        """Scan CRM CSVs for stage transitions since last snapshot."""
        if not self.crm_dir or not self.crm_dir.exists():
            logger.warning("CRM directory not configured or missing: %s", self.crm_dir)
            return []

        old_snapshot = self._load_snapshot()
        current = self._read_crm()
        changes = self._diff(old_snapshot, current)
        self._save_snapshot(current)

        events = []
        for change in changes:
            entity_id = change.get("client_id") or change.get("company_id", "")
            if entity_id:
                events.append(OutcomeEvent(
                    entity_id=entity_id,
                    event_name=change["event"],
                    reward=change["reward"],
                    details=change,
                ))

        logger.info("CRM resolver: %d changes → %d events", len(changes), len(events))
        return events

    def _load_snapshot(self) -> Dict:
        snapshot_file = self.snapshot_dir / "crm_snapshot.json"
        if not snapshot_file.exists():
            return {"deals": {}, "leads": {}}
        try:
            with open(snapshot_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load CRM snapshot: %s", e)
            return {"deals": {}, "leads": {}}

    def _save_snapshot(self, snapshot: Dict):
        snapshot_file = self.snapshot_dir / "crm_snapshot.json"
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    def _read_crm(self) -> Dict:
        """Read current CRM state from CSVs."""
        deals_path = self.crm_dir / "relationships" / "deals.csv"
        leads_path = self.crm_dir / "relationships" / "leads.csv"

        deals = {}
        for row in _read_csv(deals_path):
            deal_id = row.get("deal_id", "").strip()
            if deal_id:
                stage = row.get("stage", "").strip().lower()
                if row.get("paid_date", "").strip() and stage != "paid":
                    stage = "paid"
                deals[deal_id] = {
                    "stage": stage,
                    "client_id": row.get("client_id", "").strip(),
                    "name": row.get("name", "").strip(),
                    "value": row.get("value", "").strip(),
                }

        leads = {}
        for row in _read_csv(leads_path):
            lead_id = row.get("lead_id", "").strip()
            if lead_id:
                leads[lead_id] = {
                    "stage": row.get("stage", "").strip().lower(),
                    "company_id": row.get("company_id", "").strip(),
                    "estimated_value": row.get("estimated_value", "").strip(),
                }

        return {"deals": deals, "leads": leads}

    def _diff(self, old: Dict, current: Dict) -> List[Dict]:
        """Detect stage transitions between old and current CRM state."""
        changes = []

        for deal_id, deal in current.get("deals", {}).items():
            old_deal = old.get("deals", {}).get(deal_id)
            if old_deal is None:
                continue
            old_stage = old_deal.get("stage", "")
            new_stage = deal.get("stage", "")
            if old_stage and new_stage and old_stage != new_stage:
                match = _match_transition(old_stage, new_stage, DEAL_TRANSITIONS)
                if match:
                    event, reward = match
                    changes.append({
                        "type": "deal",
                        "id": deal_id,
                        "client_id": deal.get("client_id", ""),
                        "from_stage": old_stage,
                        "to_stage": new_stage,
                        "event": event,
                        "reward": reward,
                        "name": deal.get("name", ""),
                    })

        for lead_id, lead in current.get("leads", {}).items():
            old_lead = old.get("leads", {}).get(lead_id)
            if old_lead is None:
                continue
            old_stage = old_lead.get("stage", "")
            new_stage = lead.get("stage", "")
            if old_stage and new_stage and old_stage != new_stage:
                match = _match_transition(old_stage, new_stage, LEAD_TRANSITIONS)
                if match:
                    event, reward = match
                    changes.append({
                        "type": "lead",
                        "id": lead_id,
                        "company_id": lead.get("company_id", ""),
                        "from_stage": old_stage,
                        "to_stage": new_stage,
                        "event": event,
                        "reward": reward,
                    })

        return changes
