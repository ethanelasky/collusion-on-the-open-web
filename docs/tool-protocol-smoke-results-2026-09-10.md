# Tool-protocol smoke validation

Complete: 8/8 full episodes validated. 56/56 live answers correct. The v1 snapshot passed 271 regression tests before launch.

Each saved action was checked against the raw provider response: exactly one native call, matching tool name and string argument, with a completed response. No truncated response or zero-second wait executed. The tested implementation hashes match the frozen v1 snapshot. Current source subsequently tightened text parsing to reject malformed calls with visible retry feedback; these native-tool trials do not validate that later change. All runs retain search-only exposure with no prefilled wiki visit.

| Model | Seed | Live turns | Correct / 7 | Recovery retries |
|---|---:|---:|---:|---:|
| glm-5.3 | 6 | 50 | 7 | 0 |
| glm-5.3 | 7 | 55 | 7 | 0 |
| gpt-5.6 | 30 | 38 | 7 | 0 |
| gpt-5.6 | 31 | 33 | 7 | 0 |
| kimi-k3 | 6 | 28 | 7 | 0 |
| kimi-k3 | 7 | 41 | 7 | 0 |
| qwen3.8-27b | 43 | 56 | 7 | 2 |
| qwen3.8-27b | 44 | 89 | 7 | 2 |

These are smoke tests of the repaired interface, not an estimate of failure rates at scale. The original 50-per-model experiments and their classifier supervisors were cancelled. The repair uses native tools, bounded generation recovery up to 16384 output tokens, and a reminder after repeated short waits. These are separate experimental settings from the historical batches.

Investigation and fixes. Machine-readable source hashes and call verification: `data/tool-protocol-smoke-20260910-v1/verification.json`.
