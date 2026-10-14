from pathlib import Path

ROOT = Path(__file__).parents[3]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()


def test_tag_release_cannot_bypass_browser_gate() -> None:
    assert "release-gate:" in RELEASE
    assert "needs: release-gate" in RELEASE
    assert "npm test" in RELEASE
    assert "npm run test:e2e" in RELEASE
    assert "npm run build" in RELEASE
