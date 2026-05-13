from pathlib import Path

# Scoring thresholds
NOTIFY_THRESHOLD = 6  # score >= 6 → email
LOG_THRESHOLD = 4  # score 4-5 → log only; score 0-3 → drop

# How many proposals to fetch per daily run (sorted newest-first).
# High enough to cover the full backlog on first run; deduplication handles the rest.
LAUSUNTOPALVELU_FETCH_TOP = 200

# Paths
ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"
CONTEXT_DIR = ROOT / "context"
STATE_DIR.mkdir(exist_ok=True)
CONTEXT_DIR.mkdir(exist_ok=True)
SEEN_PROPOSALS_PATH = STATE_DIR / "seen_proposals.json"
SEEN_DOCUMENTS_PATH = STATE_DIR / "seen_documents.json"
SCORE_LOG_PATH = STATE_DIR / "score_log.jsonl"
FLAGGED_PATH = STATE_DIR / "nostetut.json"
CONTEXT_PATH = CONTEXT_DIR / "kuluttajaliitto.json"

# Committee pages – these are the main pages that embed schedule + agenda data
COMMITTEE_URLS = {
    "talousvaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/talousvaliokunta",
    "maa_ja_metsatalousvaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/maa-ja-metsatalousvaliokunta",
    "ymparistovaliokunta": "https://www.eduskunta.fi/kansanedustajat-ja-toimielimet/valiokunnat/ymparistovaliokunta",
}

COMMITTEE_DISPLAY_NAMES = {
    "talousvaliokunta": "Talousvaliokunta",
    "maa_ja_metsatalousvaliokunta": "Maa- ja metsätalousvaliokunta",
    "ymparistovaliokunta": "Ympäristövaliokunta",
}
