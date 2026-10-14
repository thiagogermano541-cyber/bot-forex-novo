import type {Analysis} from "@/lib/api";

export function verdictLabel(analysis?: Analysis): string {
  if (!analysis) return "AGUARDAR";
  const labels: Record<string, string> = {"CENÁRIO DE ALTA": "COMPRA", "CENÁRIO DE BAIXA": "VENDA"};
  return analysis.verdict || labels[analysis.decision] || "AGUARDAR";
}

export function FinalVerdict({analysis, processing, freshnessSeconds = 300}: {
  analysis?: Analysis; processing: boolean; freshnessSeconds?: number;
}) {
  const verdict = verdictLabel(analysis);
  const scenario = analysis?.data.scenarios.find(s => s.direction === (verdict === "COMPRA" ? "ALTA" : verdict === "VENDA" ? "BAIXA" : "LATERAL"));
  const old = !!analysis && Date.now() - Date.parse(analysis.created_at) > freshnessSeconds * 1000;
  const tone = verdict === "COMPRA" ? "buy" : verdict === "VENDA" ? "sell" : "wait";
  return <section className={`panel final-verdict ${tone}`} aria-live="polite">
    <div className="verdict-heading"><span className="eyebrow">VEREDITO FINAL · BTC/USD</span><span className="mini-label">EXPERIMENTAL</span></div>
    <h2>{processing ? "ANALISANDO…" : verdict}</h2>
    {analysis ? <>
      <small>{new Date(analysis.created_at).toLocaleString("pt-BR", {timeZone:"UTC"})} UTC · {analysis.decision}</small>
      {old && <p className="verdict-age">Análise anterior. Solicite uma nova análise para avaliar o mercado atual.</p>}
      <p>{analysis.data.summary}</p>
      {scenario && verdict !== "AGUARDAR" && <div className="verdict-conditions">
        <div><h4>Confirmar antes de agir</h4>{scenario.confirmation.map(c => <p key={c}>{c}</p>)}</div>
        <div><h4>Invalidação</h4><p>{scenario.invalidation}</p>{scenario.invalidation_price && <strong>{new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(scenario.invalidation_price)}</strong>}</div>
      </div>}
    </> : <p>Clique em Solicitar análise. O Claude avaliará os dados reais e devolverá um veredito validado.</p>}
  </section>;
}
