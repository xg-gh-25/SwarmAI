/**
 * Ingestion service mapper tests.
 *
 * Verifies one recursive mapper converts every nested backend snake_case field
 * without changing arrays or scalar values.
 */
import { describe, expect, it } from 'vitest';
import { mapIngestionValue } from './ingestion';

describe('mapIngestionValue', () => {
  it('maps nested objects and arrays to camelCase', () => {
    expect(mapIngestionValue({
      total_units: 2,
      nested_rows: [{ oldest_wait_seconds: 12, alreadyCamel: true }],
    })).toEqual({
      totalUnits: 2,
      nestedRows: [{ oldestWaitSeconds: 12, alreadyCamel: true }],
    });
  });
});
