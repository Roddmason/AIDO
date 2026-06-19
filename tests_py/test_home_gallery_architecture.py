from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "local-control-center" / "web" / "src" / "features" / "home"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_home_gallery_cards_are_extracted_components() -> None:
    expected = [
        "HomePage.tsx",
        "WorkspaceCard.tsx",
        "RunCard.tsx",
        "ReviewCard.tsx",
        "RuntimeBlockerCard.tsx",
        "homeModel.ts",
    ]
    for name in expected:
        assert (HOME / name).exists(), name


def test_home_count_logic_lives_in_a_pure_helper_module() -> None:
    model = read(HOME / "homeModel.ts")
    # Pure module: no React / DOM dependency, so the count + ordering logic is unit-testable.
    assert "from 'react'" not in model
    assert "buildHomeGallery" in model
    for helper in (
        "selectActiveProjects",
        "selectPendingReviews",
        "selectRuntimeBlockers",
        "selectRecentRuns",
        "projectInProgressCount",
        "projectPendingReviewCount",
    ):
        assert helper in model, helper

    # HomePage consumes the pure helper instead of re-implementing counts inline.
    home = read(HOME / "HomePage.tsx")
    assert "from './homeModel'" in home
    assert "buildHomeGallery" in home


def test_home_is_card_first_without_tables_or_evidence_jargon() -> None:
    home = read(HOME / "HomePage.tsx")
    assert "DataTable" not in home
    assert "<table" not in home
    assert "masonry-grid" in home
    # The retired control-plane "Recent evidence" band must not return to the landing.
    assert "evidenceTitle" not in home
    assert "Recent evidence" not in home
