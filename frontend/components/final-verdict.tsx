import type {Analysis,Market} from "@/lib/api";

export function verdictLabel(analysis?: Analysis): string {
  if (!analysis) return "AGUARDAR";
  const labels: Record<string, string> = {"CENÁRIO DE ALTA": "COMPRA", "CENÁRIO DE BAIXA": "VENDA"};
  return analysis.verdict || labels[analysis.decision] || "AGUARDAR";
}

export function FinalVerdict({analysis, market, processing, freshnessSeconds = 300}: {
  analysis?: Analysis; market?: Market; processing: boolean; freshnessSeconds?: number;
}) {
  const saved = verdictLabel(analysis);
  const plan = analysis?.data.trade_plan;
  const reference = analysis?.data.market_reference;
  const expired = !!reference?.valid_until && Date.parse(reference.valid_until) <= Date.now();
  const q = market?.snapshot?.quote;
  const outside = !!plan && !!q && ((q.bid+q.ask)/2 < plan.entry_low || (q.bid+q.ask)/2 > plan.entry_high);
  const staleData = !!market && (market.status !== "ATUALIZADO" || !q || Date.now()-Date.parse(q.timestamp)>90000);
  const blocked = !!plan && plan.status === "CONFIRMADO" && (expired || outside || staleData);
  const verdict = blocked ? "AGUARDAR" : saved;
  const scenario = analysis?.data.scenarios.find(s => s.direction === (verdict === "COMPRA" ? "ALTA" : verdict === "VENDA" ? "BAIXA" : "LATERAL"));
  const old = !!analysis && Date.now() - Date.parse(analysis.created_at) > freshnessSeconds * 1000;
  const tone = verdict === "COMPRA" ? "buy" : verdict === "VENDA" ? "sell" : "wait";
  return <section className={`panel final-verdict ${tone}`} aria-live="polite">
    <div className="verdict-heading"><span className="eyebrow">VEREDITO FINAL · BTC/USD</span><span className="mini-label">EXPERIMENTAL</span></div>
    <h2>{processing ? "ANALISANDO…" : verdict}</h2>
    {analysis ? <>
      <small>{new Date(analysis.created_at).toLocaleString("pt-BR", {timeZone:"UTC"})} UTC · {analysis.decision}</small>
      {old && <p className="verdict-age">Análise anterior. Solicite uma nova análise para avaliar o mercado atual.</p>}
      {blocked && <p className="verdict-age">Veredito salvo: {saved}. {expired ? "Plano vencido." : staleData ? "Dados atuais insuficientes." : "Preço atual fora da região de entrada."} Solicite nova análise.</p>}
      <p>{analysis.data.summary}</p>
      <PlanDetails analysis={analysis}/>
      {!plan && scenario && verdict !== "AGUARDAR" && <div className="verdict-conditions">
        <div><h4>Confirmar antes de agir</h4>{scenario.confirmation.map(c => <p key={c}>{c}</p>)}</div>
        <div><h4>Invalidação</h4><p>{scenario.invalidation}</p>{scenario.invalidation_price && <strong>{new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(scenario.invalidation_price)}</strong>}</div>
      </div>}
    </> : <p>Clique em Solicitar análise. O Claude avaliará os dados reais e devolverá um veredito validado.</p>}
  </section>;
}


const usd = (value:number) => new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(value);
export function PlanDetails({analysis}:{analysis?:Analysis}) {
  if (!analysis) return null;
  const plan = analysis.data.trade_plan;
  const ref = analysis.data.market_reference;
  if (!plan) return null;
  return <div className="signal-plan">
    <div className="verdict-heading"><h4>{plan.setup_type} · {plan.timeframe}</h4><span className="mini-label">{plan.status === "PENDENTE" ? "SETUP PENDENTE" : "CONFIRMADO NO SNAPSHOT"}</span></div>
    <div className="plan-levels"><div><small>Região de entrada · {plan.side}</small><strong>{usd(plan.entry_low)} – {usd(plan.entry_high)}</strong></div><div><small>Stop / invalidação propostos</small><strong>{usd(plan.stop_loss)}</strong></div></div>
    <h4>{plan.status === "PENDENTE" ? "Confirmação necessária" : "Evidência de confirmação"}</h4><p>{plan.confirmation}</p>
    <h4>Alvos propostos</h4>{plan.targets.map((target,index) => <div className="plan-target" key={target.price}><strong>Alvo {index+1} · {usd(target.price)}</strong><p>{target.rationale}</p>{ref?.gross_risk_reward?.[index] !== undefined && <small>R/R bruto projetado: {ref.gross_risk_reward[index].toFixed(2)} · taxas e slippage não incluídos</small>}</div>)}
    <h4>Cancelar o setup se</h4>{plan.cancel_if.map(c => <p key={c}>{c}</p>)}
    {ref && <div className="plan-reference"><small>Referência analisada: {ref.price == null ? "—" : usd(ref.price)} · dados em {new Date(ref.data_cutoff_utc).toLocaleString("pt-BR",{timeZone:"UTC"})} UTC</small>{ref.valid_until && <small>Válido até {new Date(ref.valid_until).toLocaleString("pt-BR",{timeZone:"UTC"})} UTC, enquanto as condições se mantiverem.</small>}<small>Histórico recebido pela IA: {Object.entries(ref.context_candles).map(([tf,count])=>`${tf}: ${count}`).join(" · ")}</small></div>}
  </div>;
}
