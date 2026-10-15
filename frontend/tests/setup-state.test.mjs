import test from "node:test";
import assert from "node:assert/strict";
import {setupState} from "../lib/setup-state.ts";

const now=Date.parse("2026-10-15T18:00:00Z");
const market={status:"ATUALIZADO",snapshot:{quote:{bid:99,ask:101,timestamp:new Date(now).toISOString()}}};
const analysis={verdict:"AGUARDAR",decision:"AGUARDAR",data:{trade_plan:{side:"VENDA",status:"PENDENTE",entry_low:105,entry_high:106,stop_loss:110},market_reference:{valid_until:new Date(now+600000).toISOString()}}};

test("future sell entry is visible as pending rather than an immediate sell verdict",()=>{
 const state=setupState(analysis,market,now);
 assert.equal(state.headline,"VENDA EM");assert.equal(state.pending,true);
 assert.equal(state.verdict,"AGUARDAR");assert.equal(state.inRegion,false);
});
test("price entering the region never confirms an AI setup automatically",()=>{
 const state=setupState(analysis,{...market,snapshot:{quote:{...market.snapshot.quote,bid:105,ask:106}}},now);
 assert.equal(state.inRegion,true);assert.equal(state.pending,true);assert.equal(state.verdict,"AGUARDAR");
});
test("expired, recovered, invalidated and stale setups cannot appear as current entries",()=>{
 for(const [record,mkt,time] of [
  [analysis,market,now+600001],
  [{...analysis,data:{...analysis.data,market_reference:{...analysis.data.market_reference,recovered_from_audit:"test"}}},market,now],
  [analysis,{...market,snapshot:{quote:{...market.snapshot.quote,bid:111,ask:112}}},now],
  [analysis,undefined,now],
  [analysis,market,now+90001]
 ]){const state=setupState(record,mkt,time);assert.equal(state.pending,false);assert.equal(state.headline,"AGUARDAR");}
});
test("a confirmed plan outside its entry remains blocked",()=>{
 const confirmed={...analysis,verdict:"VENDA",data:{...analysis.data,trade_plan:{...analysis.data.trade_plan,status:"CONFIRMADO"}}};
 assert.equal(setupState(confirmed,market,now).headline,"AGUARDAR");
});
