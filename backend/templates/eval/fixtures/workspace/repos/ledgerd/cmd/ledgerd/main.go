package main

import (
	"log"
	"net/http"

	"example.com/ledgerd/internal/api"
)

func main() {
	log.Fatal(http.ListenAndServe(":9180", api.Routes()))
}
