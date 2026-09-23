# Wiki link integrity in the unrun v5 condition

The user explicitly requested that the wiki not return broken links or errors,
because those make it appear to be a decoy. This implements that routing and
presentation change behind `wiki_link_integrity: true`. The flag is enabled only
in the new v5 conditions and requires HTML rendering with archive replay disabled.
Historical v4 behavior and run data are preserved.

## Findings

The existing archival background selection already included every explicit
same-wiki page dependency. Its 17 original injections did not contain missing
explicit wiki destinations. The renderer instead created accidental browse links
for ten CamelCase words, including DataUSA, OpenAI, and contributor signatures.
Those destinations had no page content and returned 404. The source inventory is
[the archive-content proposal](../../data/wiki-realism-team/archive-content-proposal.yaml).

The June 17 homepage capture itself distinguishes absent references: OpenAI is
plain text followed by an editable question-mark link through WillkommenImWiki.
See [the preserved source HTML](../../ai_collusion/fixtures/wiki/dse-startseite-20260617.html).
Inventing biographies for those names, or restoring arbitrary historical answer
pages, would not be a sound repair.

## Implemented behavior

- CamelCase becomes an automatic browse link only when its page exists at the
  current simulated time. Unknown handles and ordinary words remain plain text.
  Explicit `[[MissingPage]]` references get a question-mark creation link.
- Existing pages retain their original text. Missing-page browse/raw requests
  open an actual empty creation form and clearly say the page has no text yet.
  Merely opening that form does not create a page or revision. Saving text uses
  the existing append-only World store; it appears in browse, raw reads, index,
  search, and recent changes. No fallback imports content from retired pages.
- Homepage aliases, browse/raw, edit/save, index, search, recent changes, and
  the archived welcome/continue creation links resolve deterministically.
  Search forms work, and virtual index/search/recent-changes pages do not expose
  ineffective edit controls. Unknown local paths return a real 302 redirect to
  the DseWiki entry point. Unexposed unknown actions show available navigation.
- All these wiki routes precede external website failure rules. A broken data
  condition therefore cannot turn a local wiki page into HTTP 429/502/503.
- Simple single-URL stdout curl/wget wiki GETs use the same deterministic store,
  without invoking either a host shell or an environment model. Supported curl
  redirect-following flags and wget follow the local entry-point redirect.
  Quoted save URLs execute only as actual recognized GET commands; inspection,
  echo, pipelines, and multi-command strings cannot accidentally save a page.
- Complex shell commands retain the existing simulator. They receive current
  resolved wiki facts, including empty creation forms for absent references, and
  explicit instructions forbidding invented wiki errors, login gates, timeouts,
  or profile content. Their outputs still depend on the simulator following
  these facts and instructions; this is not a general shell interpreter or a
proof that arbitrary simulator output cannot contain a wiki error.

The subsequent user-requested discussion subpage brings v5 to 13 pages. Its
creation precedes the main-page link, both pages expose verified GET recipes,
and the refreshed five-arm/two-time crawl covers 530 internal URL visits with
zero 404s. All 16 link tests passed again after that content change.

The ordinary live page renderer and prefill preview receive matching page
inventories. The inventory updates when timed posts or live saves create pages.
User text is escaped in page bodies, names, and editor textareas.

## Validation

Local deterministic tests only; no model calls or rollouts were run.

`tests/test_wiki_links.py` has 16 passing cases. It crawls every exposed local
anchor and search form across all five v5 arms, both at opening and after all
timed posts, using the final 12-page background configuration. It also checks
missing-page creation/save/discovery, read-only safety, escaping, no resurrection
of the retired all-state answer page, original URL aliases, unknown routes and
actions, raw reads, new-page autolinks, prefill/live matching, v4 preservation,
deterministic curl/wget, and the complex-shell prompt boundary.

The new link suite plus all existing v4 realism tests passed: **31 tests**.
Existing discovery, web-fixture, sparse, sparse-v2, write-instruction, shared-wiki,
cache-prefix, timeout, and shell-interruption suites had **165 passes and one
stale timing expectation**. That expectation predated this change and incorrectly
required an unsupported multi-state fetch to charge approximately 4,878 seconds
despite a 30-second timeout. It was updated to the already-authorized September
11 timeout semantics, with an explicit timed-out request ledger, 30-second charge,
matching task-clock advance, and no missed rounds. The corrected case and the
link suite then passed together (**17 tests**). All **197 distinct tested cases**
have passed; the broader suite was not needlessly repeated after the test-only
correction.

## Comparability

This changes more than page prose. V5 removes misleading browse links, supplies
usable missing-page editors, closes local navigation, and moves simple shell
wiki GETs from simulated to deterministic responses. Those requests keep the
existing shell call overhead, but they no longer accrue model-produced latency
or environment-model calls. Complex-shell instructions also differ. These are
separate v5 treatment changes and must not be described as equivalent to v4 or
as a pure content ablation. Data failure probabilities, live question schedule,
and the existing wiki write store are unchanged by this implementation.
