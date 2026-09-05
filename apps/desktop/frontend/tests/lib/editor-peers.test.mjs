import assert from 'node:assert/strict';
import test from 'node:test';
import pnpmHooks from '../../.pnpmfile.cjs';
const { hooks } = pnpmHooks;

test('corrects only the known invalid dist-tag alternative, retaining numeric ranges', () => {
  const pkg = { name: '@tailwindcss/typography', version: '0.5.19', peerDependencies: {
    tailwindcss: '>=3.0.0 || insiders || >=4.0.0-alpha.20 || >=4.0.0-beta.1',
  } };
  assert.equal(hooks.readPackage(pkg).peerDependencies.tailwindcss,
    '>=3.0.0 || >=4.0.0-alpha.20 || >=4.0.0-beta.1');
});
test('does not broaden an unsupported version or another package peer range', () => {
  for (const [name, version] of [['tailwindcss-animate', '1.0.7'], ['unknown', '1.0.7']]) {
    const pkg = { name, version, peerDependencies: { tailwindcss: '^3.0.0' } };
    assert.equal(hooks.readPackage(pkg).peerDependencies.tailwindcss, '^3.0.0');
  }
});
