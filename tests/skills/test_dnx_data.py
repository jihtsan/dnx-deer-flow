from pathlib import Path

SKILL_FILE = Path(__file__).resolve().parents[2] / "skills" / "public" / "dnx-data" / "SKILL.md"


def test_dnx_api_requests_use_local_shell_transport() -> None:
    skill_text = SKILL_FILE.read_text(encoding="utf-8")

    assert "Use the `bash` tool and `curl` for every DNX API request" in skill_text
    assert "Never use `web_fetch` for DNX API requests" in skill_text
    assert 'BASE_URL="${BASE_URL:-http://localhost:8081}"' in skill_text
    assert "Always attempt the required `curl` request before deciding that `BASE_URL` is unreachable" in skill_text
    assert "Do not infer current reachability from memory or from an earlier `web_fetch` failure" in skill_text
    assert "Do not ask the user for another URL or copied JSON before this local `curl` attempt" in skill_text
