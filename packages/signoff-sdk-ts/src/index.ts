// Signoff TypeScript SDK.
//
// Source of truth for wire-format types is `docs/protocol.md` §3 in the
// Signoff monorepo. The Python `signoff-core` package defines these same
// models; their JSON Schemas (copied into `src/schemas/` at build time)
// are asserted to agree with the Zod schemas below.

export const VERSION = '0.0.1';

export {
  BlockerEntrySchema,
  ClaimSchema,
  DELIVERABLE_CLAIM_ID,
  DeliverableSchema,
  FeedbackPacketSchema,
  ID_PATTERN,
  RESERVED_CLAIM_KINDS,
  SeveritySchema,
  VERIFIER_NAME_PATTERN,
  VerdictSchema,
  VerifierResultSchema,
  WarningEntrySchema,
  type BlockerEntry,
  type Claim,
  type Deliverable,
  type FeedbackPacket,
  type Severity,
  type Verdict,
  type VerifierResult,
  type WarningEntry,
} from './models';
