package ledger

import "errors"

// Posting moves Amount minor units (cents) into Account; a negative Amount moves money out.
type Posting struct {
	Account int64
	Amount  int64
}

var ErrUnbalanced = errors.New("postings do not sum to zero")

// Transfer returns the two postings for moving amount from one account to another.
func Transfer(from, to, amount int64) ([]Posting, error) {
	p := []Posting{{Account: from, Amount: -amount}, {Account: to, Amount: amount}}
	var sum int64
	for _, x := range p {
		sum += x.Amount
	}
	if sum != 0 {
		return nil, ErrUnbalanced
	}
	return p, nil
}
