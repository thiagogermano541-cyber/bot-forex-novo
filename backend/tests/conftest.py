import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-only-secret-at-least-thirty-two-characters"
os.environ["ADMIN_PASSWORD"] = "test-only-admin-password"
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
