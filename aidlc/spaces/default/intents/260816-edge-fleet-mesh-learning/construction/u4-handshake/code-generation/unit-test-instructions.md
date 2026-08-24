# Unit test instructions — U4 handshake

From the MindRoom repo root:

```sh
cd /Users/dwayne/mindroom
.venv/bin/python -m pytest tests/test_handshake_inspect.py --tb=short -p no:cacheprovider -n0
```

## Acceptance mapped to tests

| Check | Test |
|-------|------|
| Schema `mindroom.handshake-inspect/1` | `test_c8_schema_is_handshake_inspect_v1` |
| Missing OpenClaw → surface_absent, FR4.2 false | `test_c8_surface_absent_when_openclaw_missing` |
| pairing/nodes/gateway-connect is not a usable mesh handshake | `test_c8_pairing_nodes_gateway_connect_is_not_a_usable_surface` |
| Leftover MindRoom callable without OpenClaw mesh marker does not bind | `test_c8_existing_callable_without_openclaw_mesh_surface_does_not_bind` |
| Bind only if OpenClaw mesh marker **and** existing callable | `test_c8_surface_found_only_with_openclaw_mesh_marker_and_existing_callable` |
| This tree: unread switch removed (FR4.5), FR4.2 not implemented | `test_c8_fr45_unread_switch_is_removed_in_this_tree` |
| Help parser does not treat pairing as mesh-enrollment | `test_c8_probe_parses_openclaw_help_without_treating_pairing_as_mesh` |

Do not require live Tailscale or a live OpenClaw gateway process for this slice. Do not start quality/NFR.