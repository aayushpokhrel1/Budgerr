# Fails when HANDOVER.md accumulates. The fix is ALWAYS to move content to its real home
# (README / PRODUCT / docs/OPERATIONS / docs/ARCHITECT / the vault), never to raise MAX.
#
# This exists because the same file on a sibling project (FamilyRecipes) reached 1,365 lines
# by accumulating everything, started contradicting itself, and a session then acted on a
# superseded line and re-proposed something the same file recorded as already tried and
# rejected. A doc asking for brevity does not get it; a red test does.
import re
from pathlib import Path

MAX = 200

# tests/ -> backend/ -> repo root. HANDOVER.md is gitignored, so it is often absent
# (a fresh clone, CI, another machine) and the test must pass when it is.
HANDOVER = Path(__file__).resolve().parents[2] / "HANDOVER.md"


def test_handover_stays_a_current_state_note():
    if not HANDOVER.exists():
        return
    lines = HANDOVER.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX, (
        f"HANDOVER.md is {len(lines)} lines (max {MAX}). Move content to its home, "
        f"do not raise MAX."
    )
    dated = [l for l in lines if re.match(r"^#{1,4} .*\d{4}-\d{2}-\d{2}", l)]
    assert not dated, (
        f"dated headings belong in git log, not the handover: {dated}"
    )
