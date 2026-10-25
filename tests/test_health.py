from fastapi.testclient import TestClient

from app import main


def test_health_reports_service_status(monkeypatch) -> None:
    monkeypatch.setattr(main, "database_health", lambda: "ok")
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "backend-careeros"
