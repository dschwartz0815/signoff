// Copy JSON schemas from signoff-core into the SDK so the SDK is
// self-contained and Windows contributors don't hit symlink issues. Run
// before build, test, and typecheck via the package.json hooks.
//
// This is *not* symlinked: the schemas are treated as a versioned input
// to the SDK. Drift between the two copies is impossible in practice
// because the monorepo workflow rebuilds on every commit.

import { copyFileSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', 'signoff-core', 'src', 'signoff', 'schemas');
const dst = resolve(here, '..', 'src', 'schemas');

mkdirSync(dst, { recursive: true });

const files = readdirSync(src).filter((f) => f.endsWith('.json'));
if (files.length === 0) {
  console.error(`No schemas found in ${src}`);
  process.exit(1);
}
for (const f of files) {
  copyFileSync(join(src, f), join(dst, f));
}
console.log(`Copied ${files.length} schema(s) into ${dst}`);
