"""Execution checks on Claude proposals. Never select direction or move prices."""

def assess_setup(plan, spread=0, minimum_rr=1.5):
    report = dict(status="SEM_SETUP", reasons=[], minimum_gross_rr=minimum_rr,
                  gross_risk_reward=[], policy_version=1)
    if not plan:
        return report
    reasons = report["reasons"]
    buy = plan["side"] == "COMPRA"
    entry = plan["entry_high"] if buy else plan["entry_low"]
    risk = abs(entry - plan["stop_loss"])
    rewards = [(t["price"]-entry if buy else entry-t["price"]) for t in plan["targets"]]
    ratios = [reward/risk if risk else 0 for reward in rewards]
    report["gross_risk_reward"] = ratios
    if not ratios or ratios[0] < minimum_rr:
        reasons.append(f"Primeiro alvo oferece R/R bruto {ratios[0] if ratios else 0:.2f}, abaixo do mínimo {minimum_rr:.2f}.")
    if rewards and rewards[0] <= (spread or 0):
        reasons.append("Distância até o primeiro alvo não cobre sequer o spread observado.")
    trigger = plan.get("trigger")
    if not trigger:
        reasons.append("Plano sem gatilho estruturado: não foi possível verificar a relação entre confirmação e entrada.")
    else:
        kind, level = trigger["kind"], trigger["level"]
        if kind in ("ROMPIMENTO_ALTA", "RETESTE_ALTA") and not buy:
            reasons.append("Gatilho de alta incompatível com plano de venda.")
        if kind in ("ROMPIMENTO_BAIXA", "RETESTE_BAIXA") and buy:
            reasons.append("Gatilho de baixa incompatível com plano de compra.")
        if kind == "ROMPIMENTO_ALTA" and plan["entry_low"] < level:
            reasons.append("Entrada de compra abaixo do gatilho de rompimento; falta reteste explícito.")
        if kind == "ROMPIMENTO_BAIXA" and plan["entry_high"] > level:
            reasons.append("Entrada de venda acima do gatilho de rompimento; falta reteste explícito.")
        if kind in ("RETESTE_ALTA", "RETESTE_BAIXA", "REJEICAO") and not plan["entry_low"] <= level <= plan["entry_high"]:
            reasons.append("Região de reteste/rejeição não contém o nível estrutural do gatilho.")
        if kind == "OBSERVADO" and plan["status"] != "CONFIRMADO":
            reasons.append("Gatilho observado exige confirmação atual; plano ainda pendente.")
        if trigger.get("volume_multiple") is not None and not trigger.get("volume_baseline"):
            reasons.append("Filtro de volume sem base observada disponível.")
    report["status"] = "REPROVADO" if reasons else "APTO"
    return report
