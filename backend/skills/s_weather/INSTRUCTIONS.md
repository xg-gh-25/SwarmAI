# Weather

**Why?** Quick weather checks without leaving the terminal -- useful for daily briefings, travel planning, or deciding if you need an umbrella.

**No API key required.** Uses wttr.in (curl-based) and Open-Meteo (JSON API) -- both free and keyless.

---

## Quick Start

```
"What's the weather in Tokyo?" -> current conditions + 3-day forecast
"Will it rain tomorrow in Sydney?" -> focused forecast
```

---

## Workflow

### Step 1: Parse Location

Extract the location from the user's request. Accept:
- City names: "Tokyo", "San Francisco", "London"
- City + country: "Melbourne, Australia"
- Airport codes: "SFO", "NRT", "LHR"
- Landmarks: "Eiffel Tower", "Central Park"
- Coordinates: "35.6762,139.6503"

If no location specified, ask the user. Do NOT guess.

### Step 2: Fetch Weather Data

Use wttr.in for human-readable output:

```bash
# One-line current conditions
curl -s "wttr.in/{location}?format=%l:+%c+%t+%h+%w"

# Detailed current + 3-day forecast (compact)
curl -s "wttr.in/{location}?format=v2"

# JSON format for programmatic parsing
curl -s "wttr.in/{location}?format=j1"
```

**URL encoding:** Replace spaces with `+` in location names.
- "San Francisco" -> `curl -s "wttr.in/San+Francisco?format=v2"`
- "New York" -> `curl -s "wttr.in/New+York?format=v2"`

### Step 3: Present Results

Format as a concise, readable summary:

```
Weather in Tokyo, Japan

Now: Partly cloudy, 22C (72F), Humidity 65%, Wind 12 km/h NE
Today: 18-24C, afternoon showers likely
Tomorrow: 16-22C, clearing up
Day after: 15-23C, sunny

Sunrise: 05:48 | Sunset: 18:12
```

**Rules:**
- Always show both Celsius and Fahrenheit
- Lead with current conditions
- Include "feels like" temperature if significantly different from actual
- For rain queries, lead with precipitation info
- Keep it to 5-8 lines max unless user wants detail

### Step 4: Answer Specific Questions

| User Asks | Focus On |
|-----------|----------|
| "Is it raining?" | Current precipitation + next few hours |
| "Do I need a jacket?" | Temperature + wind chill + rain chance |
| "Weekend forecast" | Saturday + Sunday only |
| "Best day to go outside" | Compare upcoming days, pick lowest rain + best temp |
| "Weather for my trip" | Multi-day forecast for the duration |

---

## Advanced: Open-Meteo (JSON API)

For more precise data or when wttr.in is rate-limited:

```bash
# Current weather by coordinates
curl -s "https://api.open-meteo.com/v1/forecast?latitude=35.6762&longitude=139.6503&current_weather=true&timezone=auto"

# Hourly forecast
curl -s "https://api.open-meteo.com/v1/forecast?latitude=35.6762&longitude=139.6503&hourly=temperature_2m,precipitation_probability,weathercode&timezone=auto&forecast_days=3"
```

Use Open-Meteo when:
- User needs hourly breakdown
- wttr.in returns errors or is rate-limited
- Precise coordinates are available

**Geocoding** (location name to coordinates):
```bash
curl -s "https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
```

---

## Multi-Location Comparison

When user asks about multiple locations (e.g., "weather in Tokyo vs Seoul"):

```bash
# Run in parallel
curl -s "wttr.in/Tokyo?format=j1" &
curl -s "wttr.in/Seoul?format=j1" &
wait
```

Present as a side-by-side comparison table.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| wttr.in returns HTML | Add `?format=` parameter, or use `curl -H "Accept: text/plain"` |
| Rate limited (429) | Wait 30s and retry, or switch to Open-Meteo |
| Location not found | Try alternate spelling, add country, or use airport code |
| Garbled output | Ensure terminal supports UTF-8; try `?format=j1` for JSON |
| "Unknown location" | Use coordinates instead: `wttr.in/35.6762,139.6503` |

---

## Quality Rules

- Always include the location name in the response (confirm what was looked up)
- Never present raw JSON or curl output to user -- always format nicely
- If request is ambiguous (e.g., "Melbourne" = Australia or Florida?), ask
- Rate limits: wttr.in allows ~100 requests/day per IP. Space out bulk requests
- Timezone: mention local time at the location if relevant to the query

