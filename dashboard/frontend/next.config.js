/** @type {import('next').NextConfig} */

// Requests to the API go through app/api/backend/[...path]/route.ts, not a
// rewrite. A rewrite forwards but cannot add a header, and the shared token
// has to be added on the server — the browser must never see it.

const nextConfig = {};

module.exports = nextConfig;
