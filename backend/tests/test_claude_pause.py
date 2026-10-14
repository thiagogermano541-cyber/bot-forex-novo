from app.config import Settings


def test_operator_pause_blocks_claude_readiness():
    cfg = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15, claude_enabled=False)
    assert not cfg.claude_ready


def test_configured_enabled_claude_is_ready():
    cfg = Settings(anthropic_api_key="TEST_ONLY", claude_model="TEST_MODEL",
                   claude_input_usd_per_million=3, claude_output_usd_per_million=15, claude_enabled=True)
    assert cfg.claude_ready
