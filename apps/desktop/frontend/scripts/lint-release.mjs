import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint } from 'eslint';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const baseline = JSON.parse(await readFile(path.join(root, 'eslint-warning-baseline.json'), 'utf8'));
const scoped = /^(src\/(services\/(workdayReadiness|intelligence|contextConnector)|components\/(ConnectorReadiness|WorkdayReadinessCard|ContextConnectorSettings|EnterpriseConnectionsCenter|IntelligencePanel|About))|tests\/ui\/)/;
const eslint = new ESLint({ cwd: root });
const results = await eslint.lintFiles(['src', 'tests']);
const counts = {};
let failures = 0;
for (const result of results) {
  const relative = path.relative(root, result.filePath).replaceAll('\\', '/');
  for (const message of result.messages) {
    const key = `${relative}|${message.ruleId}`;
    if (message.severity === 2 || scoped.test(relative)) {
      failures += 1;
      console.error(`${relative}:${message.line} ${message.ruleId}: release lint violation`);
    } else {
      counts[key] = (counts[key] ?? 0) + 1;
    }
  }
}
for (const [key, count] of Object.entries(counts)) {
  if (count > (baseline.warnings[key] ?? 0)) {
    failures += 1;
    console.error(`${key}: warning baseline exceeded`);
  }
}
console.log(`Release lint: ${failures} violations; ${Object.values(counts).reduce((a, b) => a + b, 0)} isolated upstream warnings`);
process.exitCode = failures ? 1 : 0;
