"""API tests on the frozen gold snapshot with the rule-based baseline. No API key needed."""
import os
from pathlib import Path

os.environ["BATCHGUARD_AUTOCREATE"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    app = create_app(llm_kind="rule", lake=ROOT / "evals" / "fixtures",
                     regs=Path(__file__).parent / "fixtures_regs.json", audit_path=tmp_path / "audit.db")
    return TestClient(app)


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["model"] == "rule-based-baseline"


def test_investigate_returns_contract(client):
    r = client.post("/investigate", json={"batch_id": 92})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["summary"]["probable_cause_equipment_id"] == "PH-102"


def test_full_approval_flow_and_audit_trail(client):
    run_id = client.post("/investigate", json={"batch_id": 92}).json()["run_id"]
    r = client.post(f"/investigations/{run_id}/approve",
                    json={"reviewer": "QA Reviewer", "decision": "APPROVED", "comment": "Calibration lapse confirmed"})
    assert r.status_code == 200
    detail = client.get(f"/investigations/{run_id}").json()
    nodes = [t["node"] for t in detail["audit_trail"]]
    assert nodes[0] == "intake" and nodes[-1] == "human_review"
    assert detail["approvals"][0]["decision"] == "APPROVED"


def test_cannot_approve_twice(client):
    run_id = client.post("/investigate", json={"batch_id": 92}).json()["run_id"]
    body = {"reviewer": "QA Reviewer", "decision": "APPROVED", "comment": "first decision"}
    assert client.post(f"/investigations/{run_id}/approve", json=body).status_code == 200
    assert client.post(f"/investigations/{run_id}/approve", json=body).status_code == 409


def test_cannot_approve_no_deviation(client):
    run_id = client.post("/investigate", json={"batch_id": 45}).json()["run_id"]
    r = client.post(f"/investigations/{run_id}/approve",
                    json={"reviewer": "QA Reviewer", "decision": "APPROVED", "comment": "nothing to approve"})
    assert r.status_code == 409


def test_approval_requires_reviewer_and_comment(client):
    run_id = client.post("/investigate", json={"batch_id": 92}).json()["run_id"]
    r = client.post(f"/investigations/{run_id}/approve", json={"reviewer": "", "decision": "APPROVED", "comment": ""})
    assert r.status_code == 422


def test_bad_input_rejected_not_crashed(client):
    r = client.post("/investigate", json={"batch_id": "92; DROP TABLE batches"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED_INPUT"


def test_unknown_run_is_404(client):
    assert client.get("/investigations/nope").status_code == 404


# ---------------- reviewer workflow (Power Automate) ----------------
@pytest.fixture
def flow_client(tmp_path, monkeypatch):
    calls = []

    class Resp:
        status_code = 202

    def fake_post(url, json=None, timeout=None):
        calls.append(dict(url=url, json=json))
        return Resp()

    monkeypatch.setattr("api.main.httpx.post", fake_post)
    app = create_app(llm_kind="rule", lake=ROOT / "evals" / "fixtures",
                     regs=Path(__file__).parent / "fixtures_regs.json", audit_path=tmp_path / "audit.db",
                     flow_url="https://flow.example/trigger", public_base="https://abc.trycloudflare.com",
                     approval_token="s3cret-token")
    return TestClient(app), calls


def test_review_needed_triggers_flow_with_callback(flow_client):
    client, calls = flow_client
    body = client.post("/investigate", json={"batch_id": 92}).json()
    assert len(calls) == 1
    sent = calls[0]["json"]
    assert sent["approve_url"] == f"https://abc.trycloudflare.com/investigations/{body['run_id']}/approve"
    assert "PH-102" in sent["details"] and "CAL-00096" in sent["details"]
    nodes = [t["node"] for t in client.get(f"/investigations/{body['run_id']}").json()["audit_trail"]]
    assert "notify_reviewer" in nodes


def test_no_flow_call_when_nothing_to_review(flow_client):
    client, calls = flow_client
    client.post("/investigate", json={"batch_id": 45})
    client.post("/investigate", json={"batch_id": 999})
    assert calls == []


def test_approval_token_enforced(flow_client):
    client, _ = flow_client
    run_id = client.post("/investigate", json={"batch_id": 92}).json()["run_id"]
    body = {"reviewer": "QA Reviewer", "decision": "APPROVED", "comment": "Calibration lapse confirmed"}
    assert client.post(f"/investigations/{run_id}/approve", json=body).status_code == 401
    assert client.post(f"/investigations/{run_id}/approve", json=body,
                       headers={"X-Approval-Token": "wrong"}).status_code == 401
    assert client.post(f"/investigations/{run_id}/approve", json=body,
                       headers={"X-Approval-Token": "s3cret-token"}).status_code == 200


def test_flow_failure_does_not_break_investigation(tmp_path, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("flow unreachable")

    monkeypatch.setattr("api.main.httpx.post", boom)
    app = create_app(llm_kind="rule", lake=ROOT / "evals" / "fixtures",
                     regs=Path(__file__).parent / "fixtures_regs.json", audit_path=tmp_path / "audit.db",
                     flow_url="https://flow.example/trigger")
    client = TestClient(app)
    r = client.post("/investigate", json={"batch_id": 92})
    assert r.status_code == 200 and r.json()["status"] == "PENDING_APPROVAL"
    trail = client.get(f"/investigations/{r.json()['run_id']}").json()["audit_trail"]
    assert any(t["node"] == "notify_reviewer" and "error" in t["output"] for t in trail)
