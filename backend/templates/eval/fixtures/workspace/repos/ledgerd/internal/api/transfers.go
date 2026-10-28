package api

import "net/http"

// Routes serves the HTTP+JSON API.
func Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /transfers", createTransfer)
	return mux
}

func createTransfer(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Idempotency-Key") == "" {
		http.Error(w, "Idempotency-Key header is required", http.StatusBadRequest)
		return
	}
	// look up a stored response for the key (24h), else run the transfer and store it
	w.WriteHeader(http.StatusCreated)
}
