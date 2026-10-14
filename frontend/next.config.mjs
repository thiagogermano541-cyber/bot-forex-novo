const backend = process.env.BACKEND_URL || "http://localhost:8000";
export default {
  output: "standalone",
  async rewrites() { return [{source: "/api/:path*", destination: `${backend}/:path*`}]; },
  async headers() { return [{source: "/:path*", headers: [
    {key: "X-Frame-Options", value: "DENY"}, {key: "X-Content-Type-Options", value: "nosniff"},
    {key: "Referrer-Policy", value: "same-origin"}
  ]}]; }
};
