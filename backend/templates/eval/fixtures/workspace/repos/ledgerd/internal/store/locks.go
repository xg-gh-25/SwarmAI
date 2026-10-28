package store

import (
	"context"
	"database/sql"
	"sort"
)

// LockAccounts takes a transaction-scoped advisory lock on each account, lowest id first.
func LockAccounts(ctx context.Context, tx *sql.Tx, ids ...int64) error {
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	for _, id := range ids {
		if _, err := tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock($1)", id); err != nil {
			return err
		}
	}
	return nil
}
