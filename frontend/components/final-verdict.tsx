import type {Analysis,Market} from "@/lib/api";
import {setupState} from "@/lib/setup-state";

export function verdictLabel(analysis?: Analysis): string {
  if (!analysis) return "AGUARDAR";
  const labels: Record<string, string> = {"CENÁRIO DE ALTA": "COMPRA", "CENÁRIO DE BAIXA": "VENDA"};
  return analysis.verdict || labels[analysis.decision] || "AGUARDAR";
}

export function FinalVerdict({analysis, market, processing, freshnessSeconds = 300}: {
  analysis?: Analysis; market?: Market; processing: boolean; freshnessSeconds?: number;
}) {
  const plan = analysis?.data.trade_plan;
  const state = setupState(analysis, market);
  const {saved, recovered, blocked, pending, verdict} = state;
  const scenario = analysis?.data.scenarios.find(s => s.direction === (verdict === "COMPRA" ? "ALTA" : verdict === "VENDA" ? "BAIXA" : "LATERAL"));
  const old = !!analysis && Date.now() - Date.parse(analysis.created_at) > freshnessSeconds * 1000;
  const tone = state.tone === "COMPRA" ? "buy" : state.tone === "VENDA" ? "sell" : "wait";
  return <section className={`panel final-verdict ${tone}`} aria-live="polite">
    <div className="verdict-heading"><span className="eyebrow">{pending ? "SETUP PREPARADO" : "VEREDITO FINAL"} · BTC/USD</span><span className="mini-label">{pending ? "PENDENTE · EXPERIMENTAL" : "EXPERIMENTAL"}</span></div>
    <h2>{processing ? "PREPARANDO SETUP…" : state.headline}</h2>
    {pending && plan && !processing && <><div className="setup-entry">{plan.entry_low === plan.entry_high ? usd(plan.entry_low) : `${usd(plan.entry_low)} – ${usd(plan.entry_high)}`}</div><p className="verdict-age">{state.inRegion ? "Preço na região proposta; aguarde o gatilho descrito abaixo." : "Plano condicional pronto. A entrada depende da confirmação abaixo."}</p></>}
    {analysis ? <>
      <small>{new Date(analysis.created_at).toLocaleString("pt-BR", {timeZone:"UTC"})} UTC · {analysis.decision}</small>
      {recovered && !blocked && <p className="verdict-age">Resposta recuperada, com os dados e o horário originais.</p>}
      {old && !pending && <p className="verdict-age">Análise anterior. Solicite uma nova análise para avaliar o mercado atual.</p>}
      {blocked && <p className="verdict-age">Veredito salvo: {saved}. {state.reason} Solicite nova análise.</p>}
      {plan && <PlanDetails analysis={analysis}/>}
      {!plan && !processing && <p className="verdict-age">Sem setup defensável nesta análise. Consulte abaixo o impedimento informado pelo Claude.</p>}
      <p>{analysis.data.summary}</p>
      {!plan && scenario && verdict !== "AGUARDAR" && <div className="verdict-conditions">
        <div><h4>Confirmar antes de agir</h4>{scenario.confirmation.map(c => <p key={c}>{c}</p>)}</div>
        <div><h4>Invalidação</h4><p>{scenario.invalidation}</p>{scenario.invalidation_price && <strong>{new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(scenario.invalidation_price)}</strong>}</div>
      </div>}
    </> : <p>Clique em Montar setup BTC. O Claude avaliará os dados reais e devolverá um veredito validado.</p>}
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
