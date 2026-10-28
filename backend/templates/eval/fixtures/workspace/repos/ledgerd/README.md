# ledgerd

ledgerd records money transfers between accounts as a double-entry ledger: every
transfer is two postings that sum to zero, and an account's balance is the sum of its
postings. It serves an HTTP+JSON API on port 9180 and keeps everything in Postgres.

## Running

    make run        # needs DATABASE_URL
    make test
