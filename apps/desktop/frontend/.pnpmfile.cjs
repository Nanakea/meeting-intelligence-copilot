// These two upstream manifests put a dist-tag inside a SemVer range. Remove
// only that invalid alternative; retain every numeric compatibility constraint.
module.exports = {
  hooks: {
    readPackage(pkg) {
      const known = {
        '@tailwindcss/typography@0.5.19': '>=3.0.0 || insiders || >=4.0.0-alpha.20 || >=4.0.0-beta.1',
        'tailwindcss-animate@1.0.7': '>=3.0.0 || insiders',
      };
      const expected = known[`${pkg.name}@${pkg.version}`];
      if (expected && pkg.peerDependencies?.tailwindcss === expected) {
        pkg.peerDependencies.tailwindcss = expected.split(' || ').filter((range) => range !== 'insiders').join(' || ');
      }
      return pkg;
    },
  },
};
