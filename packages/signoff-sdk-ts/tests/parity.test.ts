// TypeScript side of the cross-language parity test. Reads the shared
// fixtures under `tests/parity/fixtures/` (relative to repo root) and
// runs them through the Zod schemas with the same expectations as the
// Python side.

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { ZodError } from 'zod';

import {
  BlockerEntrySchema,
  ClaimSchema,
  DeliverableSchema,
  FeedbackPacketSchema,
  VerdictSchema,
  VerifierResultSchema,
  WarningEntrySchema,
} from '../src/models';

const here = resolve(fileURLToPath(import.meta.url), '..');
const FIXTURES = resolve(here, '..', '..', '..', 'tests', 'parity', 'fixtures');
const MANIFEST = JSON.parse(readFileSync(resolve(FIXTURES, '_manifest.json'), 'utf-8')) as {
  valid: Record<string, string>;
  invalid: string[];
};

const SCHEMAS: Record<string, ZodTypeAny> = {
  Deliverable: DeliverableSchema,
  Claim: ClaimSchema,
  VerifierResult: VerifierResultSchema,
  Verdict: VerdictSchema,
  FeedbackPacket: FeedbackPacketSchema,
  BlockerEntry: BlockerEntrySchema,
  WarningEntry: WarningEntrySchema,
};

function canonical(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(obj).sort()) out[k] = sortKeys(obj[k]);
    return out;
  }
  return value;
}

describe('parity: valid fixtures round-trip', () => {
  for (const [stem, modelName] of Object.entries(MANIFEST.valid)) {
    it(`${stem} (${modelName})`, () => {
      const raw = readFileSync(resolve(FIXTURES, `${stem}.json`), 'utf-8');
      const loaded = JSON.parse(raw);
      const schema = SCHEMAS[modelName];
      if (!schema) throw new Error(`unknown model in manifest: ${modelName}`);
      const parsed = schema.parse(loaded);
      const canonicalParsed = canonical(parsed);
      // Parsing the canonical form back through the schema must yield
      // the same canonical output.
      const reParsed = schema.parse(JSON.parse(canonicalParsed));
      expect(canonical(reParsed)).toBe(canonicalParsed);
      // And the fixture itself (canonicalized) must match. Catches
      // SDK/core drift in defaults, field ordering of nullable fields,
      // etc.
      expect(canonical(loaded)).toBe(canonicalParsed);
    });
  }
});

describe('parity: invalid fixtures raise ZodError about the expected field', () => {
  for (const stem of MANIFEST.invalid) {
    it(`${stem}`, () => {
      const payload = JSON.parse(readFileSync(resolve(FIXTURES, `${stem}.json`), 'utf-8'));
      const meta = JSON.parse(readFileSync(resolve(FIXTURES, `${stem}.meta.json`), 'utf-8')) as {
        model: string;
        expect_error_on: string;
      };
      const schema = SCHEMAS[meta.model];
      if (!schema) throw new Error(`unknown model in meta: ${meta.model}`);
      try {
        schema.parse(payload);
      } catch (err) {
        if (!(err instanceof ZodError)) throw err;
        const paths = err.issues.map((i) => i.path.join('.'));
        const messages = err.issues.map((i) => i.message);
        const expected = meta.expect_error_on;
        expect(
          paths.some((p) => p.includes(expected)) || messages.some((m) => m.includes(expected)),
          `expected an error on/about ${expected}; got paths=${JSON.stringify(paths)} messages=${JSON.stringify(messages)}`,
        ).toBe(true);
        return;
      }
      throw new Error(`${stem}: parse should have thrown`);
    });
  }
});
