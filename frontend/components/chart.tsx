"use client";
import {useEffect,useRef} from "react";
import {CandlestickSeries,ColorType,createChart,type UTCTimestamp} from "lightweight-charts";
import type {Candle,Zone} from "@/lib/api";
export function MarketChart({candles,zones}:{candles:Candle[];zones:Zone[]}){
 const root=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(!root.current||!candles.length)return;const chart=createChart(root.current,{autoSize:true,height:340,layout:{background:{type:ColorType.Solid,color:"#0e111a"},textColor:"#8490aa",attributionLogo:true},grid:{vertLines:{color:"#181d2a"},horzLines:{color:"#181d2a"}},rightPriceScale:{borderColor:"#252b3c"},timeScale:{borderColor:"#252b3c",timeVisible:true},localization:{locale:"pt-BR"}});const series=chart.addSeries(CandlestickSeries,{upColor:"#40d8b4",downColor:"#fa6c8d",borderVisible:false,wickUpColor:"#40d8b4",wickDownColor:"#fa6c8d"});series.setData(candles.map(c=>({time:Math.floor(Date.parse(c.timestamp)/1000) as UTCTimestamp,open:c.open,high:c.high,low:c.low,close:c.close})));zones.forEach(z=>{const color=z.kind==="COMPRADORES"?"#40d8b4":"#ae87ff";[z.low,z.high].forEach(price=>series.createPriceLine({price,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title:z.kind}));});chart.timeScale().fitContent();return()=>chart.remove();},[candles,zones]);
 return candles.length?<div ref={root} aria-label="Gráfico de candles; horários UTC"/>:<div className="empty chart-empty"><span className="grid-orb">◇</span><strong>Aguardando dados de mercado</strong><p>O gráfico aparecerá quando o provedor fornecer candles reais.</p></div>;
}
