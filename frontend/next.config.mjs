export default {
  output: "standalone",
  async headers() { return [{source: "/:path*", headers: [
    {key: "X-Frame-Options", value: "DENY"}, {key: "X-Content-Type-Options", value: "nosniff"},
    {key: "Referrer-Policy", value: "same-origin"}
  ]}]; }
};
