from fastapi.testclient import TestClient

from medical_archive.main import create_app


def test_health_endpoint_returns_ready():
    response = TestClient(create_app(testing=True)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
