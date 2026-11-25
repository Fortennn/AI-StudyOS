def test_health_endpoint(client):
    """Test 1.1: Health check returns valid JSON schema and correct mode without calling models."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["provider"] in ["mock", "gemini"]
    assert data["storage"] == "ok"
    assert "uptime_s" in data
    assert "vector_index" in data["extra"]

def test_generate_api_flow(client):
    """Test 1.2: End-to-end flow from HTTP request to MockProvider and back to client."""
    payload = {
        "text": "Реляційні бази даних зберігають інформацію у вигляді нормалізованих таблиць з ключами.",
        "topic": "Бази даних",
        "card_count": 3,
        "quiz_count": 3
    }
    response = client.post("/api/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] == "success"
    assert data["topic"] == "Бази даних"
    assert "request_id" in data
    
    # Verify summary structure
    assert "tldr" in data["summary"]
    assert len(data["summary"]["key_takeaways"]) >= 2
    
    # Verify flashcards
    assert len(data["flashcards"]) == 3
    for card in data["flashcards"]:
        assert "front" in card
        assert "back" in card
        assert "category" in card
        
    # Verify quiz
    assert len(data["quiz"]) == 3
    for q in data["quiz"]:
        assert len(q["options"]) == 4
        assert 0 <= q["correct_index"] <= 3
        assert "explanation" in q
