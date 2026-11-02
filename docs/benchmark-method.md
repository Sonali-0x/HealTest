# Benchmark method

The benchmark runs a seeded, fixed matrix over the three checked-in sample apps.
Each app is copied to a temporary directory, mutated there, and served only for
that case; the source fixtures are never edited. The default matrix has 42
generated Selenium cases: seven mutations against two distinct controls in
each of `todo`, `shop`, and `multistep-form`.

Supported UI changes are renamed IDs, changed button labels, moved elements,
added wrappers, a cookie modal, an extra confirmation step, and a button
replaced by a link. Each generated test asserts the relevant interaction, so a
case counts as healed only when its test passes.

## Comparison arms

- **No healing:** execute the broken test once. This is the zero-healing
  reference arm.
- **Heuristic:** extract the intended ID from the broken locator, choose an
  exact or unambiguous renamed-ID candidate from the captured DOM, and require
  that candidate to be unique. It also dismisses or continues through injected
  UI gates when those controls appear in the captured DOM. No model is called.
- **HealTest:** execute the normal evidence, diagnosis, safety, and verified
  patch workflow. Only a passing verification rerun counts as healed. Attempt
  tokens and rejected unsafe patches are collected from persisted run records.

The benchmark job runs trusted, generated fixtures inside the worker and uses
the worker's local execution mode for these isolated cases. It does not accept
arbitrary test code or application paths from the benchmark request. A live
model endpoint is required to measure live model behavior; mock mode results
must be identified as mock-mode results.

## Metrics

The JSON report includes healing rate, average attempts, median seconds,
rejected unsafe suggestions, total tokens, tokens per successful heal, and
optional cost per successful heal. Cost is omitted unless a price per 1,000
tokens is supplied. The report and dashboard label mock versus live model mode
and whether required provider settings are present. Metrics are reported for
each arm and mutation type.
Every unhealed case includes its error text; setup and browser failures are not
silently counted as successful repairs.

Run the complete matrix from the repository root:

```powershell
python -m benchmark.runner --seed 42
```

This writes `benchmark/results.json` and `benchmark/results.svg`. Options:

```powershell
python -m benchmark.runner --seed 42 --apps todo,shop --mutations renamed_id,button_text
python -m benchmark.runner --seed 42 --cost-per-1k-tokens 0.02
```

The dashboard submits the same seeded run through `POST /benchmarks`, polls
`GET /benchmarks/{id}`, and displays the strategy chart, mutation breakdown,
tokens/cost, and unhealed HealTest cases. Requests can select apps and mutation
types through the API; the dashboard uses all defaults.

The seed fixes case selection and execution order. Runtime measurements and
provider responses naturally vary between runs.
