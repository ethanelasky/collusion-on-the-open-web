# DeepSeek and GLM request rates: September 13

The user requested higher request rates for the two unfinished color-game
samples. Both campaigns now use eight rollout workers, increased from two.
DeepSeek has a target of 20 HTTP request starts per minute. GLM has a target of
60. Retries use the same pacing. An admission gate reduces concurrent requests
after provider throttling and recovers after successful requests.

The previous last-hour model-action start rates were about 7.6 per minute for
DeepSeek and 24.6 for GLM. These counts exclude internal HTTP retries. The new
execution records every HTTP attempt, success, failure, and retry. Thus the
old action-start count and the new HTTP-attempt count are different measures.
Eight workers or a target RPM does not guarantee provider capacity.

The initial live check found about 56 HTTP starts per minute for GLM with no
429 responses. DeepSeek continued to receive 429 responses and entered the
shared cooldown. Longer measurements are saved in the execution records.

Both models keep their Inceptron route, original API key, model identifier,
reasoning setting, prompts, target plans, counter rules, and action limits.
The alternate OpenRouter key had less available credit and no verified higher
quota. OpenRouter states that extra accounts or keys do not increase globally
managed capacity. [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits)

This is an operational change to the async setting. Alice still finishes
before Bob starts. Counters remain separate for each rollout. The game has no
wall-clock deadline in this setting. Higher concurrency can change provider
latency and the frequency of API errors; those effects must be considered when
comparing execution periods.

## Preserved data and fixed samples

The old supervisors could not drain active work without interruption. A
controlled stop retained these partial jobs without replay or replacement:

| Model | Complete at stop | Interrupted job indices | Fixed sample |
| --- | ---: | --- | ---: |
| DeepSeek Flash v4 | 108 | 107, 109 | 200 |
| GLM 5.3 | 280 | 280, 281 | 1,450 |

The GLM complete count includes one rollout with errors. All 2,160 saved
DeepSeek files and 5,600 GLM files from completed jobs passed checksum checks
before and after restart. Interrupted trajectories remain in the sample.
Absent rounds count as incorrect under the existing failure rule.

The original request, launch, source manifest, source snapshot, confirmation
plans, and planned denominators remain unchanged. Each new execution copies
the original source and changes only the campaign orchestration module plus
the new acceleration script. It resumes queued jobs with empty output
directories. An immutable operational receipt authorizes the worker-count
change and records hashes of both source sets and the fixed plans.

The new supervisors use the same stop file, campaign output, and progress
files. The cost monitor, public Docent uploader, and final analysis watcher
continue to use those files. The full-study analysis still waits for all five
fixed samples. It does not test incomplete DeepSeek or GLM samples.

## Execution records

The study root is
`reports/color-game/six-models-3x50-20260912-074117/`.

- DeepSeek: `confirmation-async-2x200-20260912-165704/deepseek-v4-flash-high/executions/paced-eight-20260913/`, initial PID 16838.
- GLM: `efficient-five-async-20260912-180236/glm-5.3-high/executions/paced-eight-20260913/`, initial PID 16893.
- Operational records: `efficient-five-async-20260912-180236/acceleration-20260913-042708/`.

Each execution has `operational-plan.json`, source hashes, a start receipt,
`requests.jsonl`, `retry-events.jsonl`, and `admission-status.json`. The JSONL
logs contain request timing and status only. The original rollout files retain
the full transcripts. Operational records include completed-file checksums,
interrupted job identifiers, and verification after restart.

Use `scripts/accelerate_color_campaign.py prepare`, then `verify`, then
`start`. Preparation makes no model calls. Start requires the original
supervisor lock to be free and a frozen plan for live models. It rejects
changes to the saved game or model settings and refuses to replay partial
jobs. This path is restricted to `async_counter`.

Validation passed: 77 acceleration, campaign, and CLI tests. These include
source tampering, lock ownership, detached stub execution, queued-only resume,
thread-local request controls, and preservation of complete and partial jobs.
The completed-model statistical release passed a separate 128-test suite.

- [Completed Sol, Astra, and Luna tests](color-game-completed-results-2026-09-13.md)
- [Live five-model progress](http://bubble:8003/efficient-five-async-20260912-180236/)
