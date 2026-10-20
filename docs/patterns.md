# Writing review patterns

A pattern is a short YAML file that encodes a datasheet rule for one part: "the
EN pin must not float", "OE# must be tied to ground". Patterns run on every
**Check**, for free, without a model, and their findings are checked against
the netlist, so they never hallucinate. They're the easiest way to contribute:
no Python needed.

Patterns live in [`src/pcb_assistant/patterns/library/`](../src/pcb_assistant/patterns/library/).

## Example

```yaml
id: lmr51625-buck
part: LMR51625 synchronous buck regulator
status: trusted
datasheet: https://www.ti.com/lit/gpn/lmr51625
match:
  value_contains: "51625"

checks:
  - id: en-not-floating
    rule: pin_connected
    pinfunction: EN
    severity: must-fix
    citation: >-
      LMR51625 datasheet, Table 5-1 (Pin Functions, EN) and §7.3.3 (Enable):
      the EN pin must be tied to VIN or a UVLO resistor divider and must not float.
    failure_mode: >-
      A floating EN leaves the regulator in an indeterminate enable state, so the
      buck may never start.
```

## Fields

| Field | Meaning |
|---|---|
| `id` | Unique, kebab-case. Finding ids become `<pattern id>:<check id>`. |
| `part` | Human-readable part name. |
| `status` | `trusted` (reviewed by a human) or `provisional` (not yet). A provisional pattern's must-fix is shown as *verify*. New patterns start `provisional` unless you've checked them on a real board. |
| `datasheet` | Canonical datasheet URL. |
| `match.value_contains` | Case-insensitive substring of the symbol's Value field, e.g. `"51625"`. |
| `match.ref_prefix` | Optional reference prefix, e.g. `"U"`. |
| `checks[]` | The rules, below. |

Each check has `id`, `rule`, `severity` (`must-fix`, `verify` or `cosmetic`),
`citation`, `failure_mode`, and the parameters its rule needs.

Address a pin by **number** (`pin: "1"`) or by **function** (`pinfunction: EN`).
Function matching ignores KiCad's `_<pin>` suffix, so `EN` matches `EN_2`.

## Rules

| Rule | Parameters | Fires when |
|---|---|---|
| `pin_connected` | pin | the pin is floating: no net, an `unconnected-` net, or alone on its net |
| `pin_not_connected` | pin | the pin should be a no-connect but is wired |
| `pins_share_net` | pin, `other_pin` / `other_pinfunction` | the two pins aren't on the same net |
| `pin_on_net` | pin, `expect_net_contains` | the pin's net name doesn't contain the text (e.g. `"GND"`, `"3.3"`) |
| `component_between` | pin, `value_contains` | no part whose Value contains the text touches the pin's net (e.g. a `"10k"` pull-up) |

A genuinely new kind of check needs a new rule in
[`runner.py`](../src/pcb_assistant/patterns/runner.py). Open an issue first.

## Guidelines

- **Cite precisely.** Section, table or figure, and a one-line paraphrase of the
  rule. Don't paste long passages; the datasheet is the vendor's copyright.
- **Be conservative with must-fix.** Reserve it for things that stop the board
  working. Use `verify` for "usually a mistake, but could be intentional".
- **Mind the matcher.** `value_contains` should be specific enough not to catch
  unrelated parts.
- **No false alarms.** A pattern must stay silent on a correct design. Add a
  test, or at least run it against a real board that uses the part.

## Testing

```bash
pytest tests/test_patterns.py tests/test_patterns_breadth.py
pcb check path/to/board.kicad_sch     # run every pattern on your own board
```
