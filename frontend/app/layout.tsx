import type {Metadata} from "next";
import "./globals.css";
export const metadata:Metadata={title:"JARVIS AI • Market intelligence",description:"Análise auditável de BTC/USD com Claude."};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="pt-BR"><body>{children}</body></html>}
