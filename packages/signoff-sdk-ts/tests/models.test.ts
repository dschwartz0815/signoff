import { describe, expect, it } from 'vitest';

import {
  BlockerEntrySchema,
  ClaimSchema,
  DELIVERABLE_CLAIM_ID,
  DeliverableSchema,
  FeedbackPacketSchema,
  RESERVED_CLAIM_KINDS,
  SeveritySchema,
  VerdictSchema,
  VerifierResultSchema,
  WarningEntrySchema,
} from '../src/models';

// --- §3.1 ID regex ---------------------------------------------------------

describe('§3.1 id regex', () => {
  const goodIds = ['a', '0', 'dlv_01HXYZ', 'Z-x-9', 'x'.repeat(128)];
  const badIds = [
    '',
    '_leading_underscore',
    '-leading-dash',
    'has space',
    'x'.repeat(129),
    'emoji-🚫',
  ];

  for (const id of goodIds) {
    it(`accepts ${JSON.stringify(id)}`, () => {
      expect(() => DeliverableSchema.parse({ id, kind: 'k', content: null })).not.toThrow();
    });
  }
  for (const id of badIds) {
    it(`rejects ${JSON.stringify(id.length > 20 ? `${id.slice(0, 17)}...` : id)}`, () => {
      expect(() => DeliverableSchema.parse({ id, kind: 'k', content: null })).toThrow();
    });
  }

  it('synthetic deliverable id is not a valid wire-format id', () => {
    expect(DELIVERABLE_CLAIM_ID).toBe('__deliverable__');
    expect(() =>
      ClaimSchema.parse({ id: DELIVERABLE_CLAIM_ID, text: '', kind: 'citation' }),
    ).toThrow();
  });
});

// --- §3.2 Deliverable ------------------------------------------------------

describe('§3.2 Deliverable', () => {
  it('accepts minimal fields and applies defaults', () => {
    const d = DeliverableSchema.parse({ id: 'dlv_1', kind: 'research_report', content: { a: 1 } });
    expect(d.metadata).toEqual({});
    expect(d.created_at).toBeNull();
  });

  it('accepts any JSON-serializable content', () => {
    for (const payload of [{ a: 1 }, [1, 2], 'text', 3, 3.14, true, null]) {
      expect(() =>
        DeliverableSchema.parse({ id: 'dlv_1', kind: 'k', content: payload }),
      ).not.toThrow();
    }
  });

  it('validates created_at as ISO-8601', () => {
    expect(() =>
      DeliverableSchema.parse({
        id: 'dlv_1',
        kind: 'k',
        content: null,
        created_at: '2026-04-18T14:22:10Z',
      }),
    ).not.toThrow();
    expect(() =>
      DeliverableSchema.parse({ id: 'dlv_1', kind: 'k', content: null, created_at: 'not a date' }),
    ).toThrow();
  });
});

// --- §3.3 Claim + §3.3.1 kinds ---------------------------------------------

describe('§3.3 Claim', () => {
  for (const kind of RESERVED_CLAIM_KINDS) {
    it(`accepts reserved kind ${kind}`, () => {
      expect(() => ClaimSchema.parse({ id: 'clm_1', text: 't', kind })).not.toThrow();
    });
  }

  it('accepts pack-namespaced kinds', () => {
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'legal.clause_reference' }),
    ).not.toThrow();
  });

  for (const kind of ['', 'unscoped_unknown', 'Legal.Foo', 'legal.', '.clause', 'has space.x']) {
    it(`rejects ${JSON.stringify(kind)}`, () => {
      expect(() => ClaimSchema.parse({ id: 'clm_1', text: 't', kind })).toThrow();
    });
  }

  it('requires non-negative ordered span', () => {
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'citation', span: [0, 10] }),
    ).not.toThrow();
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'citation', span: [-1, 5] }),
    ).toThrow();
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'citation', span: [10, 5] }),
    ).toThrow();
  });

  it('restricts provenance to reserved values', () => {
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'citation', provenance: 'agent_asserted' }),
    ).not.toThrow();
    expect(() =>
      ClaimSchema.parse({ id: 'clm_1', text: 't', kind: 'citation', provenance: 'guessed' }),
    ).toThrow();
  });
});

// --- §3.4 Severity ---------------------------------------------------------

describe('§3.4 Severity', () => {
  it('enum values', () => {
    expect(SeveritySchema.parse('blocker')).toBe('blocker');
    expect(SeveritySchema.parse('warning')).toBe('warning');
    expect(SeveritySchema.parse('info')).toBe('info');
    expect(() => SeveritySchema.parse('critical')).toThrow();
  });
});

// --- §3.5 VerifierResult ---------------------------------------------------

describe('§3.5 VerifierResult', () => {
  const base = {
    verifier: 'pack.name',
    claim_id: 'clm_1',
    passed: true,
    severity: 'info' as const,
    reason: 'ok',
    cost_usd: 0,
    duration_ms: 5,
  };

  it('enforces verifier name pattern', () => {
    expect(() => VerifierResultSchema.parse({ ...base, verifier: 'Pack.Name' })).toThrow();
    expect(() => VerifierResultSchema.parse({ ...base, verifier: 'no_dot' })).toThrow();
    expect(() => VerifierResultSchema.parse({ ...base, verifier: 'pack.name.extra' })).toThrow();
  });

  it('blocker failure requires non-null suggestion', () => {
    expect(() =>
      VerifierResultSchema.parse({
        ...base,
        passed: false,
        severity: 'blocker',
        reason: 'bad',
        suggestion: null,
      }),
    ).toThrow(/§3\.5 invariant/);
    expect(() =>
      VerifierResultSchema.parse({
        ...base,
        passed: false,
        severity: 'blocker',
        reason: 'bad',
        suggestion: 'fix it',
      }),
    ).not.toThrow();
  });

  it('passed non-info requires evidence', () => {
    expect(() => VerifierResultSchema.parse({ ...base, severity: 'warning' })).toThrow(
      /§3\.5 invariant/,
    );
    expect(() =>
      VerifierResultSchema.parse({ ...base, severity: 'warning', evidence: { note: 'x' } }),
    ).not.toThrow();
  });

  it('rejects negative cost and duration', () => {
    expect(() => VerifierResultSchema.parse({ ...base, cost_usd: -0.01 })).toThrow();
    expect(() => VerifierResultSchema.parse({ ...base, duration_ms: -1 })).toThrow();
  });
});

// --- §3.7 FeedbackPacket ---------------------------------------------------

describe('§3.7 FeedbackPacket / BlockerEntry / WarningEntry', () => {
  const blocker = {
    claim_id: 'clm_1',
    claim_text: 'A claim.',
    verifier: 'pack.name',
    issue: 'Source URL returned HTTP 404.',
    suggested_repair: 'Replace or remove the claim.',
  };

  it('passed is always false', () => {
    const p = FeedbackPacketSchema.parse({
      blockers: [blocker],
      cost_usd: 0,
      protocol_version: '0.1',
    });
    expect(p.passed).toBe(false);
    expect(() =>
      FeedbackPacketSchema.parse({
        passed: true,
        blockers: [],
        cost_usd: 0,
        protocol_version: '0.1',
      }),
    ).toThrow();
  });

  it('protocol version must be dotted', () => {
    expect(() =>
      FeedbackPacketSchema.parse({
        blockers: [blocker],
        cost_usd: 0,
        protocol_version: 'unstable',
      }),
    ).toThrow();
  });

  it('entries require non-empty issue + repair', () => {
    expect(() => BlockerEntrySchema.parse({ ...blocker, issue: '' })).toThrow();
    expect(() => WarningEntrySchema.parse({ ...blocker, suggested_repair: '' })).toThrow();
  });

  it('retry_budget_remaining non-negative', () => {
    expect(() =>
      FeedbackPacketSchema.parse({
        blockers: [blocker],
        cost_usd: 0,
        retry_budget_remaining: -1,
        protocol_version: '0.1',
      }),
    ).toThrow();
  });
});

// --- §3.6 Verdict ----------------------------------------------------------

describe('§3.6 Verdict', () => {
  const base = {
    id: 'vrd_1',
    deliverable_id: 'dlv_1',
    passed: true,
    results: [],
    feedback_packet: null,
    cost_usd: 0,
    duration_ms: 0,
    protocol_version: '0.1',
    started_at: '2026-04-18T14:22:10Z',
    completed_at: '2026-04-18T14:22:10Z',
  };

  it('minimal passing verdict OK', () => {
    expect(() => VerdictSchema.parse(base)).not.toThrow();
  });

  it('feedback required when failed', () => {
    expect(() => VerdictSchema.parse({ ...base, passed: false })).toThrow(/§3\.6 invariant/);
  });

  it('cost_usd must equal sum of result costs', () => {
    const r = {
      verifier: 'pack.name',
      claim_id: 'clm_1',
      passed: true,
      severity: 'info' as const,
      reason: 'ok',
      cost_usd: 0.25,
      duration_ms: 1,
    };
    expect(() => VerdictSchema.parse({ ...base, results: [r, r], cost_usd: 0.5 })).not.toThrow();
    expect(() => VerdictSchema.parse({ ...base, results: [r, r], cost_usd: 1.0 })).toThrow(
      /§3\.6 invariant/,
    );
  });
});
