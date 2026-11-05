# Water Sample Ranker

Python 3.14; uv; ruff; ty; pytest. Project root is this directory.
Never edit or commit data/, gdis_loader*, root production Excel or private artifacts.
Read docs/SPEC.md and docs/OPEN_QUESTIONS.md before changing business rules.
UNKNOWN is never FALSE or zero. No final score/rank until methodology approval.
Keep CLI/GUI on the same service. Workers communicate by Qt signals only.
All source selection is conservative, with cell provenance and lifecycle boundaries.
Run uv sync --locked, ruff check, ruff format --check, ty check, pytest.
Never upload production data or claim native Excel/Windows acceptance without evidence.
Public publication requires explicit owner authorization and a passing public privacy gate.
