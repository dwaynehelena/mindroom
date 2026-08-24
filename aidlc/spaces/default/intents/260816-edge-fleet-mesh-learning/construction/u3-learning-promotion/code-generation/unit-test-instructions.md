# Unit test instructions — U3 learning-promotion

From the MindRoom repo root:

```sh
cd /Users/dwayne/mindroom
.venv/bin/python -m pytest \
  tests/test_learning_promotion.py \
  tests/test_learning_capture.py \
  tests/test_learning_runtime.py \
  --tb=line
```

## Acceptance mapped to tests

| Check | Test |
|-------|------|
| Successful visible reply writes FlightRecorder then Learning Runtime proposes `proposed` | `test_c7_visible_reply_records_evidence_and_learning_runtime_proposes` |
| Demo-script-only origin yields no candidate | `test_c7_demo_script_only_origin_yields_no_candidate` |
| No FlightRecord → no candidate | `test_c7_no_flight_record_yields_no_candidate` |
| Failed delivery does not write or propose | `test_c7_failed_visible_delivery_does_not_write_or_propose` |
| OpenClaw Agno learning stays off | `test_fr35_openclaw_agno_learning_stays_off` |

Do not require live Tailscale or a real OpenClaw/Hermes binary for this slice. Canary/stable are later in unit, not this call.