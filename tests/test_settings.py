import pytest

from src.settings import DEFAULT_SYSTEM_PROMPT, load_settings


def required_env(**overrides: str) -> dict[str, str]:
    env = {
        "TOKEN": "123456:test-token",
        "OPENAI_API_KEY": "sk-test",
    }
    env.update(overrides)
    return env


def test_defaults_are_safe_and_consistent() -> None:
    result = load_settings(required_env())

    assert result.limit_time == 86_400
    assert result.limit_messages == 20
    assert result.history_context_messages < result.history_summary_trigger
    assert result.history_summary_trigger <= result.max_history_messages
    assert result.log_user_content is False
    assert result.group_mode == "mentions"


def test_reports_missing_required_value_clearly() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        load_settings({"TOKEN": "123456:test-token"})


def test_rejects_invalid_history_thresholds() -> None:
    with pytest.raises(ValueError, match="HISTORY_CONTEXT_MESSAGES"):
        load_settings(
            required_env(
                MAX_HISTORY_MESSAGES="20",
                HISTORY_CONTEXT_MESSAGES="10",
                HISTORY_SUMMARY_TRIGGER="10",
            )
        )


def test_empty_system_prompt_falls_back_to_default() -> None:
    assert load_settings(required_env(SYSTEM_PROMPT="")).system_prompt == DEFAULT_SYSTEM_PROMPT
    assert load_settings(required_env(SYSTEM_PROMPT=" свой ")).system_prompt == "свой"
