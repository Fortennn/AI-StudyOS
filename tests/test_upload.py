import io
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_txt_file():
    content = b"This is a test lecture about microservices and distributed systems with databases."
    files = {"file": ("lecture.txt", io.BytesIO(content), "text/plain")}
    response = client.post("/api/upload", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["filename"] == "lecture.txt"
    assert "microservices" in data["text"]
    assert data["suggested_topic"] == "Lecture"

def test_upload_empty_file():
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    response = client.post("/api/upload", files=files)
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert data["error"]["kind"] == "extraction_error"
