// Protocol data models for the Signoff TypeScript SDK.
//
// Every schema here mirrors a section of ../../docs/protocol.md §3. Wire
// format is snake_case on both sides of the boundary — this layer does
// not camel-case. If you need camelCase in application code, add a
// separate adapter; keep the protocol layer faithful to the spec.
//
// The Python side (packages/signoff-core/src/signoff/models.py) is the
// source of truth; a parity test asserts that the JSON schemas produced
// there agree structurally with what these Zod schemas accept.

import { z } from 'zod';

// ---------------------------------------------------------------------------
// §3.1 identifier regex and §7.2 verifier-name regex.
// ---------------------------------------------------------------------------

export const ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,127}$/;
export const VERIFIER_NAME_PATTERN = /^[a-z0-9_\-]+\.[a-z0-9_]+$/;

/** Reserved claim kinds from §3.3.1. Packs namespace their own kinds as `<pack>.<kind>`. */
export const RESERVED_CLAIM_KINDS = [
  'citation',
  'quantitative',
  'quote',
  'policy',
  'computational',
  'personalization',
] as const;

/** §4.3 synthetic claim id. Harness-internal; never on the wire. */
export const DELIVERABLE_CLAIM_ID = '__deliverable__';

const IdSchema = z.string().regex(ID_PATTERN, {
  message: 'id must match §3.1 regex ^[a-zA-Z0-9][a-zA-Z0-9_\\-]{0,127}$',
});
const VerifierNameSchema = z.string().regex(VERIFIER_NAME_PATTERN, {
  message: 'verifier must match §7.2 regex ^[a-z0-9_\\-]+\\.[a-z0-9_]+$',
});
const ProtocolVersionSchema = z.string().regex(/^\d+(\.\d+){1,2}$/, {
  message: 'protocol_version must be a dotted semver-ish string',
});
const Iso8601Schema = z.string().refine(
  (v) => {
    const candidate = v.endsWith('Z') ? `${v.slice(0, -1)}+00:00` : v;
    return !Number.isNaN(Date.parse(candidate));
  },
  { message: 'must be an ISO-8601 timestamp' },
);

// JSON-any — deliberately permissive because §3.2 content is "any (JSON-serializable)".
// z.unknown() is what z.any() wants to be — keeps strict typing on the caller side.
const JsonValue: z.ZodType<unknown> = z.unknown();

// ---------------------------------------------------------------------------
// §3.4 Severity
// ---------------------------------------------------------------------------

export const SeveritySchema = z.enum(['blocker', 'warning', 'info']);
export type Severity = z.infer<typeof SeveritySchema>;

// ---------------------------------------------------------------------------
// §3.2 Deliverable
// ---------------------------------------------------------------------------

export const DeliverableSchema = z
  .object({
    id: IdSchema,
    kind: z.string().min(1),
    content: JsonValue,
    metadata: z.record(z.string(), JsonValue).default({}),
    created_at: Iso8601Schema.nullable().default(null),
  })

  .describe('Implements docs/protocol.md §3.2 Deliverable.');
export type Deliverable = z.infer<typeof DeliverableSchema>;

// ---------------------------------------------------------------------------
// §3.3 Claim
// ---------------------------------------------------------------------------

const ReservedKindSet = new Set<string>(RESERVED_CLAIM_KINDS);
const PACK_NAMESPACE_PATTERN = /^[a-z0-9_\-]+\.[a-z0-9_]+$/;

const ClaimKindSchema = z
  .string()
  .min(1)
  .refine(
    (v) => ReservedKindSet.has(v) || PACK_NAMESPACE_PATTERN.test(v),
    (v) => ({
      message:
        `claim kind ${JSON.stringify(v)} is not reserved (§3.3.1) and lacks a pack namespace. ` +
        `Use one of ${JSON.stringify([...RESERVED_CLAIM_KINDS])} or namespace as <pack>.<kind>.`,
    }),
  );

const ProvenanceSchema = z
  .enum(['agent_asserted', 'extractor', 'user_supplied'])
  .nullable()
  .default(null);

const SpanSchema = z
  .tuple([z.number().int().nonnegative(), z.number().int().nonnegative()])
  .refine(([start, end]) => end >= start, {
    message: 'span end must be >= start',
  })
  .nullable()
  .default(null);

export const ClaimSchema = z
  .object({
    id: IdSchema,
    text: z.string(),
    kind: ClaimKindSchema,
    evidence: z.record(z.string(), JsonValue).default({}),
    span: SpanSchema,
    provenance: ProvenanceSchema,
  })

  .describe('Implements docs/protocol.md §3.3 Claim.');
export type Claim = z.infer<typeof ClaimSchema>;

// ---------------------------------------------------------------------------
// §3.5 VerifierResult
// ---------------------------------------------------------------------------

export const VerifierResultSchema = z
  .object({
    verifier: VerifierNameSchema,
    claim_id: IdSchema.nullable(),
    passed: z.boolean(),
    severity: SeveritySchema,
    reason: z.string().min(1),
    suggestion: z.string().nullable().default(null),
    evidence: z.record(z.string(), JsonValue).default({}),
    cost_usd: z.number().nonnegative(),
    duration_ms: z.number().int().nonnegative(),
    verifier_version: z.string().nullable().default(null),
    started_at: Iso8601Schema.nullable().default(null),
  })

  .superRefine((r, ctx) => {
    if (!r.passed && r.severity === 'blocker' && r.suggestion === null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          '§3.5 invariant: passed=false and severity=blocker requires a non-null suggestion.',
        path: ['suggestion'],
      });
    }
    if (r.passed && r.severity !== 'info' && Object.keys(r.evidence ?? {}).length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          '§3.5 invariant: passed=true with non-info severity must document the check via non-empty evidence.',
        path: ['evidence'],
      });
    }
  });
export type VerifierResult = z.infer<typeof VerifierResultSchema>;

// ---------------------------------------------------------------------------
// §3.7 FeedbackPacket + entries
// ---------------------------------------------------------------------------

const _PacketEntryObject = z.object({
  claim_id: IdSchema.nullable(),
  claim_text: z.string().nullable().default(null),
  verifier: VerifierNameSchema,
  issue: z.string().min(1),
  suggested_repair: z.string().min(1),
  evidence_excerpt: z.string().nullable().default(null),
});

export const BlockerEntrySchema = _PacketEntryObject.describe(
  'Implements docs/protocol.md §3.7 BlockerEntry.',
);
export const WarningEntrySchema = _PacketEntryObject.describe(
  'Implements docs/protocol.md §3.7 WarningEntry.',
);
export type BlockerEntry = z.infer<typeof BlockerEntrySchema>;
export type WarningEntry = z.infer<typeof WarningEntrySchema>;

export const FeedbackPacketSchema = z
  .object({
    passed: z.literal(false).default(false),
    blockers: z.array(BlockerEntrySchema).default([]),
    warnings: z.array(WarningEntrySchema).default([]),
    cost_usd: z.number().nonnegative(),
    retry_budget_remaining: z.number().int().nonnegative().nullable().default(null),
    protocol_version: ProtocolVersionSchema,
  })

  .describe('Implements docs/protocol.md §3.7 FeedbackPacket.');
export type FeedbackPacket = z.infer<typeof FeedbackPacketSchema>;

// ---------------------------------------------------------------------------
// §3.6 Verdict
// ---------------------------------------------------------------------------

export const VerdictSchema = z
  .object({
    id: IdSchema,
    deliverable_id: IdSchema,
    passed: z.boolean(),
    results: z.array(VerifierResultSchema).default([]),
    feedback_packet: FeedbackPacketSchema.nullable().default(null),
    cost_usd: z.number().nonnegative(),
    duration_ms: z.number().int().nonnegative(),
    protocol_version: ProtocolVersionSchema,
    harness_version: z.string().nullable().default(null),
    started_at: Iso8601Schema,
    completed_at: Iso8601Schema,
    terminated_early: z.boolean().default(false),
  })

  .superRefine((v, ctx) => {
    if (!v.passed && v.feedback_packet === null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: '§3.6 invariant: feedback_packet MUST be non-null when passed=false.',
        path: ['feedback_packet'],
      });
    }
    const expected = v.results.reduce((acc, r) => acc + r.cost_usd, 0);
    if (Math.abs(v.cost_usd - expected) > 1e-9) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          `§3.6 invariant: Verdict.cost_usd must equal the sum of result costs (${expected}); ` +
          `got ${v.cost_usd}.`,
        path: ['cost_usd'],
      });
    }
  })
  .describe('Implements docs/protocol.md §3.6 Verdict.');
export type Verdict = z.infer<typeof VerdictSchema>;
