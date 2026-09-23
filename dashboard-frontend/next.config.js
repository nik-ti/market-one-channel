/** @type {import('next').NextConfig} */

// The dashboard is served over HTTPS on Vercel, the API on the VPS over plain
// HTTP. A browser refuses to mix the two, so requests go to this app's own
// origin and Vercel's server forwards them — a server-to-server hop, which the
// mixed-content rule does not apply to. BACKEND_ORIGIN is a normal (non
// NEXT_PUBLIC_) variable because only the server ever reads it.
const backend = process.env.BACKEND_ORIGIN ?? "http://116.203.208.249/api/dashboard/v1";

const nextConfig = {
  async rewrites() {
    return [{ source: "/api/backend/:path*", destination: `${backend}/:path*` }];
  },
};

module.exports = nextConfig;
