import type {NextRequest} from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  // URL is deployment configuration, never an input controlled by the browser.
  const base = process.env.BACKEND_URL || "http://localhost:8000";
  const target = new URL(`${base.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;
  const headers = new Headers();
  for (const key of ["content-type", "cookie", "origin", "accept"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  try {
    const upstream = await fetch(target, {
      method: request.method, headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(240000)
    });
    const outgoing = new Headers({"Cache-Control": "no-store"});
    for (const key of ["content-type", "retry-after"]) {
      const value = upstream.headers.get(key);
      if (value) outgoing.set(key, value);
    }
    for (const cookie of upstream.headers.getSetCookie()) outgoing.append("set-cookie", cookie);
    return new Response(upstream.body, {status: upstream.status, headers: outgoing});
  } catch {
    return Response.json({detail: "Backend indisponível. Verifique a saúde dos serviços."}, {status: 503});
  }
}

export {proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as HEAD};
