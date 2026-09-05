/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false, // Disabled for BlockNote compatibility
  output: 'export',
  transpilePackages: ['@handlewithcare/prosemirror-inputrules'],
  // The pilot release has a scoped lint gate; legacy repo-wide debt remains in `pnpm lint:all`.
  eslint: {
    ignoreDuringBuilds: true,
  },
  images: {
    unoptimized: true,
  },
  // Add basePath configuration
  basePath: '',
  assetPrefix: '/',

  // Add webpack configuration for Tauri
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        path: false,
        os: false,
      };

      // Resolve public ESM exports normally; pnpm overrides keep ProseMirror single-instanced.
    }
    return config;
  },
}

module.exports = nextConfig
