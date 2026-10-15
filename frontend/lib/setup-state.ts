import type {Analysis, Market} from "./api";

export function setupState(analysis: Analysis | undefined, market: Market | undefined, now = Date.now()) {
  const labels: Record<string,string> = {"CENÁRIO DE ALTA":"COMPRA", "CENÁRIO DE BAIXA":"VENDA"};
  const saved = analysis?.verdict || labels[analysis?.decision || ""] || "AGUARDAR";
  const plan = analysis?.data.trade_plan;
  const ref = analysis?.data.market_reference;
  const q = market?.snapshot?.quote;
  const price = q ? (q.bid + q.ask) / 2 : null;
  const recovered = !!ref?.recovered_from_audit;
  const expired = !!ref?.valid_until && Date.parse(ref.valid_until) <= now;
  const stale = !market || market.status !== "ATUALIZADO" || !q || now-Date.parse(q.timestamp)>90000;
  const outside = !!plan && price !== null && (price<plan.entry_low || price>plan.entry_high);
  const invalidated = !!plan && price !== null && (plan.side === "COMPRA" ? price<=plan.stop_loss : price>=plan.stop_loss);
  const reason = recovered ? "Resposta recuperada de uma solicitação anterior."
    : expired ? "Prazo do setup encerrado."
    : invalidated ? "Cotação além do nível de invalidação proposto."
    : stale ? "Sem cotação atual para acompanhar o setup."
    : plan?.status === "CONFIRMADO" && outside ? "Preço fora da região de entrada." : "";
  const blocked = !!plan && !!reason;
  const pending = !!plan && plan.status === "PENDENTE" && !blocked;
  return {saved, recovered, blocked, pending, reason,
    verdict: blocked ? "AGUARDAR" : saved,
    headline: pending ? `${plan.side} EM` : blocked ? "AGUARDAR" : saved,
    inRegion: !!plan && price !== null && !outside && !stale && !invalidated,
    tone: pending ? plan.side : blocked ? "AGUARDAR" : saved};
}
