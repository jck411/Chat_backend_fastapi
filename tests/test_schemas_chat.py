from backend.schemas.chat import ChatCompletionRequest, ChatMessage


def test_to_openrouter_payload_preserves_web_search_fields():
    request = ChatCompletionRequest(
        model="custom/model",
        messages=[ChatMessage(role="user", content="How is the market today?")],
        plugins=[{"id": "web", "engine": "exa", "max_results": 8}],
        web_search_options={"search_context_size": "medium"},
    )

    payload = request.to_openrouter_payload(default_model="fallback/model")

    assert payload["model"] == "custom/model"
    assert payload["plugins"] == [{"id": "web", "engine": "exa", "max_results": 8}]
    assert payload["web_search_options"] == {"search_context_size": "medium"}
    assert payload["stream"] is True
