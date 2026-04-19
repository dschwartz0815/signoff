// Node helper used by test_cross_language.py.
//
// Reads one fixture, parses it through the corresponding Zod schema,
// and writes canonical JSON (sorted keys, no whitespace) to stdout.
// Exits non-zero on parse failure.
//
// Usage:  node roundtrip_node.mjs <model-name> <fixture-path>

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  BlockerEntrySchema,
  ClaimSchema,
  DeliverableSchema,
  FeedbackPacketSchema,
  VerdictSchema,
  VerifierResultSchema,
  WarningEntrySchema,
} from '@signoff/sdk';

const SCHEMAS = {
  Deliverable: DeliverableSchema,
  Claim: ClaimSchema,
  VerifierResult: VerifierResultSchema,
  Verdict: VerdictSchema,
  FeedbackPacket: FeedbackPacketSchema,
  BlockerEntry: BlockerEntrySchema,
  WarningEntry: WarningEntrySchema,
};

const [, , modelName, fixturePath] = process.argv;
if (!modelName || !fixturePath) {
  console.error('usage: node roundtrip_node.mjs <model-name> <fixture-path>');
  process.exit(2);
}
const schema = SCHEMAS[modelName];
if (!schema) {
  console.error(`unknown model ${modelName}`);
  process.exit(2);
}

const raw = readFileSync(resolve(fixturePath), 'utf-8');
const parsed = schema.parse(JSON.parse(raw));

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = sortKeys(value[k]);
    return out;
  }
  return value;
}

process.stdout.write(JSON.stringify(sortKeys(parsed)));
