import { describe, expect, it } from 'vitest';
import { VERSION } from '../src/index';

describe('signoff-sdk smoke', () => {
  it('exports a version string', () => {
    expect(VERSION).toBe('0.0.1');
  });
});
