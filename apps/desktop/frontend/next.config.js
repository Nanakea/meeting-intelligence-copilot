const path = require('path');
const tiptapPmResolveBase = path.dirname(require.resolve('@tiptap/pm/model'));
const blockNoteResolveBase = path.dirname(require.resolve('@blocknote/core'));
const resolveFromTiptapPm = (pkg) =>
  require.resolve(pkg, { paths: [tiptapPmResolveBase] });
const prosemirrorInputRulesEntry = path.resolve(
  blockNoteResolveBase,
  '..',
  '..',
  '..',
  '@handlewithcare',
  'prosemirror-inputrules',
  'dist',
  'index.js',
);

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

      // Keep ProseMirror single-instanced for BlockNote/Tiptap.
      config.resolve.alias = {
        ...config.resolve.alias,
        '@blocknote/core$': require.resolve('@blocknote/core'),
        '@blocknote/react$': require.resolve('@blocknote/react'),
        '@blocknote/shadcn$': require.resolve('@blocknote/shadcn'),
        '@handlewithcare/prosemirror-inputrules$': prosemirrorInputRulesEntry,
        'prosemirror-model': resolveFromTiptapPm('prosemirror-model'),
        'prosemirror-state': resolveFromTiptapPm('prosemirror-state'),
        'prosemirror-view': resolveFromTiptapPm('prosemirror-view'),
        'prosemirror-transform': resolveFromTiptapPm('prosemirror-transform'),
        'prosemirror-tables': resolveFromTiptapPm('prosemirror-tables'),
        'prosemirror-schema-list': resolveFromTiptapPm('prosemirror-schema-list'),
        'prosemirror-keymap': resolveFromTiptapPm('prosemirror-keymap'),
        'prosemirror-commands': resolveFromTiptapPm('prosemirror-commands'),
        'prosemirror-history': resolveFromTiptapPm('prosemirror-history'),
        'prosemirror-inputrules': resolveFromTiptapPm('prosemirror-inputrules'),
        'prosemirror-gapcursor': resolveFromTiptapPm('prosemirror-gapcursor'),
        'prosemirror-dropcursor': resolveFromTiptapPm('prosemirror-dropcursor'),
      };
    }
    return config;
  },
}

module.exports = nextConfig
