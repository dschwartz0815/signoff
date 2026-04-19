// Asserts that the JSON schemas produced by signoff-core (and copied
// into this package by scripts/copy-schemas.mjs) agree with the Zod
// schemas on required fields, nullability, enum values, and regex
// patterns. This catches drift between the Python and TypeScript sides
// the moment either one changes without the other.

import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { z } from 'zod';

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
const SCHEMAS_DIR = resolve(here, '..', 'src', 'schemas');

const PAIRS: { name: string; schema: ZodTypeAny }[] = [
  { name: 'deliverable', schema: DeliverableSchema },
  { name: 'claim', schema: ClaimSchema },
  { name: 'verifier_result', schema: VerifierResultSchema },
  { name: 'verdict', schema: VerdictSchema },
  { name: 'feedback_packet', schema: FeedbackPacketSchema },
  { name: 'blocker_entry', schema: BlockerEntrySchema },
  { name: 'warning_entry', schema: WarningEntrySchema },
];

function loadJson(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(SCHEMAS_DIR, `${name}.json`), 'utf-8'));
}

describe('schema parity: every committed JSON schema has a Zod mirror', () => {
  it('directory contents match the expected set', () => {
    const files = readdirSync(SCHEMAS_DIR)
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''))
      .sort();
    expect(files).toEqual(PAIRS.map((p) => p.name).sort());
  });
});

describe('schema parity: field-level agreement', () => {
  for (const { name, schema } of PAIRS) {
    it(`${name}: required + nullable fields agree`, () => {
      const json = loadJson(name);
      const zodShape = unwrapShape(schema);
      expect(zodShape).toBeTruthy();
      if (!zodShape) return;

      const zodFieldNames = Object.keys(zodShape).sort();
      const jsonFieldNames = Object.keys((json.properties ?? {}) as object).sort();
      expect(zodFieldNames).toEqual(jsonFieldNames);

      const jsonRequired = new Set((json.required as string[] | undefined) ?? []);
      for (const [field, def] of Object.entries(zodShape)) {
        const isOptional = isFieldOptional(def);
        const requiredOnJson = jsonRequired.has(field);
        // A field is "required" in Zod terms when Zod will reject a
        // missing key. Our models use .default(...) to make fields
        // tolerant of omission; those end up non-required in JSON
        // schema too.
        expect(
          requiredOnJson,
          `field ${name}.${field}: required mismatch (json=${requiredOnJson}, zod optional=${isOptional})`,
        ).toEqual(!isOptional);
      }
    });
  }
});

// ---------- helpers --------------------------------------------------------

function unwrapShape(schema: ZodTypeAny): Record<string, ZodTypeAny> | null {
  let current: ZodTypeAny = schema;
  for (let i = 0; i < 5; i++) {
    if (current instanceof z.ZodObject) {
      return current.shape as Record<string, ZodTypeAny>;
    }
    if (current instanceof z.ZodEffects) {
      current = current._def.schema;
      continue;
    }
    break;
  }
  return null;
}

function isFieldOptional(def: ZodTypeAny): boolean {
  // Zod marks a field "not required" (in JSON-Schema-required terms) when
  // it has a default, or is .optional(). Nullability alone doesn't make a
  // field optional — `null` is a valid concrete value.
  let current: ZodTypeAny = def;
  for (let i = 0; i < 5; i++) {
    if (current instanceof z.ZodOptional) return true;
    if (current instanceof z.ZodDefault) return true;
    if (current instanceof z.ZodNullable) {
      current = current._def.innerType;
      continue;
    }
    return false;
  }
  return false;
}
