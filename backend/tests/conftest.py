import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-only-secret-at-least-thirty-two-characters"
os.environ["ADMIN_PASSWORD"] = "test-only-admin-password"
os.environ["ADMIN_EMAIL"] = "admin@jarvis.local"
os.environ["APP_ORIGIN"] = "http://localhost:3000"
os.environ["COOKIE_SECURE"] = "false"
os.environ["CLAUDE_ENABLED"] = "true"
os.environ["KRAKEN_ENABLED"] = "false"
os.environ["ANTHROPIC_API_KEY"] = ""

from app.schemas import Candle, Quote, Snapshot, TIMEFRAMES


@pytest.fixture
def snapshot():
    now = datetime(2026, 10, 14, 12, 30, 30, tzinfo=timezone.utc)
    candles = {}
    for tf, seconds in TIMEFRAMES.items():
        last = datetime.fromtimestamp((int(now.timestamp())//seconds-1)*seconds, timezone.utc)
        candles[tf] = [Candle(id=f"test:{tf}:{i}", timestamp=last-timedelta(seconds=seconds*(29-i)),
                             timeframe=tf, open=100, high=102, low=99, close=101, volume=5,
                             volume_kind="REAL", provider="TEST ONLY") for i in range(30)]
    return Snapshot(asset="BTC/USD", captured_at=now, candles=candles,
                    quote=Quote(id="test:quote", timestamp=now-timedelta(seconds=1), bid=100, ask=101, provider="TEST ONLY"))


@pytest.fixture
def wait_analysis(snapshot):
    from app.schemas import Analysis
    source = snapshot.candles["M1"][-1].id
    return Analysis(decision="AGUARDAR", summary="A estrutura disponível ainda não confirma um cenário direcional.",
                    justification="São necessários novos dados para confirmar o rompimento da região observada.",
                    evidence=[{"category": "OBSERVADO", "description": "Candle fechado no intervalo fornecido.", "source_ids": [source]}],
                    zones=[], scenarios=[{"direction": d, "thesis": "Hipótese de teste sem decisão direcional.",
                       "confirmation": ["Novo candle deve confirmar a hipótese."], "invalidation": "Falha da confirmação.",
                       "invalidation_price": None, "evidence_ids": [source]} for d in ("ALTA", "BAIXA", "LATERAL")],
                    timeframe_context={tf: "Estrutura aguardando confirmação." for tf in TIMEFRAMES},
                    conflicts=[], risks=["Teste não representa resultado real."], missing_data=[], change_from_previous="Primeira análise.")


@pytest.fixture
def confirmed_buy(snapshot, wait_analysis):
    from app.schemas import Decision, Evidence, TradePlan, Zone
    analysis = wait_analysis.model_copy(deep=True)
    analysis.decision = Decision.BULL
    source = snapshot.candles["M1"][-1].id
    higher = snapshot.candles["H1"][-1].id
    analysis.evidence.append(Evidence(category="OBSERVADO", description="Região observada no período maior.", source_ids=[higher]))
    analysis.zones = [Zone(kind="COMPRADORES", low=100, high=101, explanation="Região observada de teste.", source_ids=[source])]
    analysis.scenarios[0].invalidation_price = 99
    analysis.trade_plan = TradePlan(side="COMPRA", status="CONFIRMADO", setup_type="Reteste da região observada",
        timeframe="M1", entry_low=100, entry_high=101, stop_loss=99,
        confirmation="Fechamento recente citado, observado na região.", confirmation_source_ids=[source],
        targets=[{"price": 102, "rationale": "Máxima na região de um candle observado.", "source_ids": [higher]}],
        valid_for_minutes=10, cancel_if=["Perda da região de invalidação."])
    return analysis
