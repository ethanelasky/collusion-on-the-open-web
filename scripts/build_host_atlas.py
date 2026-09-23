#!/usr/bin/env python3
"""Build a standalone evidence atlas from the local, sanitized wiki dump.

No network or model calls. Run from any directory with Python 3.10+.
Curated interpretations live here; all counts and quotations come from the dump.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "host-atlas"


def host(key, name, role, pattern, summary, mechanism, limits, route, quotes):
    return dict(id=key, name=name, role=role, pattern=pattern, summary=summary,
                mechanism=mechanism, limits=limits, route=route, quotes=quotes)


HOSTS = [
    host("dse", "DSE wiki", "message", r"wikiservice\.at/dse|prowiki\.org/dse",
         "Agents posted questions, answers, deadlines, and requests to runs that had reached later questions. Other writers replied on the same page or linked to a page for reporting the next answer.",
         "The posted instructions put the page name and replacement text into a GET request. RecentChanges and named task pages provided ways to find updates after reaching the wiki. Agents also proposed putting answer tokens directly into a request URL for a public log watcher.",
         "A successful wiki write is visible in this dataset. Reading, executing a quoted command, and receiving a task score generally are not. Naming the wiki in an edit is a different count from editing the wiki: DSE itself contains 13,403 stored revisions.",
         ["Writer", "GET edit", "Shared wiki page", "Reader / RecentChanges"],
         ["invitation", "first-reply", "wiki-save", "log-signal", "recentchanges"]),
    host("small-wikis", "Probier · Fractal · Dorfwiki", "message",
         r"wikiservice\.at/(probier|fractal|dorfwiki)|prowiki\.org/(probier|fractal|dorfwiki)",
         "The smaller wikis contain write tests, default placeholder pages, and lists of source links. Fractal also contains an early URL encoding an attempted request through a browser service.",
         "They offered additional pages on the same wiki infrastructure. The supplied audit found mainly write tests and saved links on these wikis. Most explicit task exchanges were on DSE.",
         "These sites contain 1,188 stored revisions in total. The much smaller citation count below measures references to their URLs, not writes on those sites. Their presence does not by itself establish migration from DSE after deletions.",
         ["Writer", "GET / test edit", "Scratch page", "Link or placeholder"],
         ["scratch", "microlink"]),
    host("counter", "api.counterapi.dev", "signal", r"counterapi\.dev",
         "Agents used a public counter as a small shared value: one key per possible answer, or a numeric code covering several answer fields. They also proposed sequential counters to measure how long a process kept running.",
         "The writer changes remote state with a GET; observers read it from other sandboxes. Wiki posts name the group of counter keys, explain what each value means, specify how to check for updates, and correct mistakes. A stored value could remain available after its sender stopped, if the request completed.",
         "The record contains reported observations, accidental test signals, rate limits, and code changes. A nonzero counter is not inherently a confirmed answer. Persistence of the service is different from proof that a detached process survived termination.",
         ["Sender sees prompt", "GET changes counter", "Shared key / value", "Watcher reads value"],
         ["counter-launch", "counter-test", "counter-query", "counter-fix", "numeric-code", "rate-limit", "heartbeat"]),
    host("countapi", "countapi.mileshilliard.com", "signal", r"countapi\.mileshilliard",
         "A backup counter service was explicitly proposed when CounterAPI failed.",
         "The posted protocol uses a key ending in a state postal code. The sender increments the counter; watchers read it through a separate URL. A separate TEST key is labeled as noise.",
         "Only three changed-text revisions mention this host. They establish a fallback plan, not successful delivery or routine adoption.",
         ["Primary fails", "Backup hit", "State-coded key", "Watcher reads"], ["backup"]),
    host("bitily", "app.bitily.in · bitily.in", "message", r"bitily\.in",
         "A URL shortener's public administration search was used as a place to find tagged replies. The wiki contains both directions to the board and a conversation about how to locate a particular reply.",
         "Agents shared a tag such as OAIJUL21PRODREPLY. Another writer tried it, reported that the short URL did not exist, and received a correction pointing to admin search. The tag identified entries in the admin search results. It was not necessarily a working short URL.",
         "Ten revisions add or replace text containing the domain, but tag-only messages are missed by a domain search. The local dump does not contain the external board's full conversations. This artifact makes no claim about present-day access.",
         ["Writer creates tagged entry", "YOURLS listing", "Admin search for tag", "Peer locates reply"],
         ["climate-tag", "yourls-invite", "yourls-missing", "yourls-correction", "yourls-contact"]),
    host("vanderbilt", "vanderbi.lt", "retrieve", r"vanderbi\.lt",
         "Agents embedded shortened data URLs inside hosted jq requests. A representative edit lists separate extraction links for Massachusetts county records in 2019, 2020, and 2021.",
         "The jq service requests the short URL, follows its redirect to the data source, and selects records. This is a different function from searching a shortener's admin listing for a reply tag.",
         "The surviving wiki text establishes constructed retrieval chains. It does not show every response from the shortener or prove that all the listed requests worked. No conversational use of this host was established by the supplied audit.",
         ["Agent", "Hosted jq", "Short URL", "County data"], ["short-jq", "short-raw"]),
    host("jqp", "jqp.vercel.app", "retrieve", r"jqp\.vercel\.app",
         "A hosted jq endpoint let agents put both a source URL and a JSON selection expression into one link. Uses range from archive manifests to filtered SEC county records.",
         "The service is an intermediary: fetch a document, run the supplied JSON filter, return a smaller result. Some links wrap a shortener or another proxy. Agents circulated these ready-made links on wiki pages.",
         "It is the most frequently cited retrieval service in this catalog under the changed-text metric. Much of the count comes from June 18 link copying. This is not a request-volume measurement or a count of independent discoveries.",
         ["Agent", "jq filter + URL", "Source / intermediary", "Selected JSON"],
         ["jq-first", "short-jq", "loop-links"]),
    host("markdown", "markdown.new", "retrieve", r"markdown\.new",
         "Agents wrapped federal-budget PDF addresses in markdown.new URLs and circulated alternate host and filename variants.",
         "A text-conversion service can make a document easier to retrieve or read through a constrained tool. The quoted edit describes the links as PDF accessibility text.",
         "A list of converter URLs proves that the routes were published. It does not prove that each URL yielded a readable document or that every use was motivated by the same restriction.",
         ["Agent", "Text converter", "PDF / page", "Readable text"], ["pdf-text"]),
    host("succ", "md.succ.ai", "retrieve", r"md\.succ\.ai",
         "An early example wraps IHME tuberculosis API queries, including location, sex, age, measure, and year selectors. A later edit places md.succ.ai and corsmirror alternatives side by side.",
         "The intermediary is embedded in the URL before the underlying health-data host. The wiki contains query URLs that another process could copy.",
         "The large citation count includes copied links and replaced lines. It does not establish how many distinct agents adopted the route, nor that the host returned correct data.",
         ["Agent", "Reader intermediary", "IHME endpoint", "Data response"], ["succ-first", "ihme-alternates"]),
    host("jina", "r.jina.ai", "retrieve", r"r\.jina\.ai",
         "The dump includes reader-proxy tests and later source-link lists using r.jina.ai. Its first changed-text appearance in this snapshot is a simple example.com test.",
         "A destination URL is passed through a reader service. The early test appears alongside archive and proxy variants for Clark economics newsletters.",
         "The first citation is a test, not evidence of successful retrieval from the task source. Distinguish the function suggested by the URL from a confirmed result.",
         ["Agent", "Reader proxy", "Target page", "Text response"], ["jina-test"]),
    host("pure", "pure.md", "retrieve", r"pure\.md",
         "Agents listed pure.md as an alternate route to federal-budget PDFs alongside markdown.new links.",
         "The full target URL is appended to the reader-service address. Publishing several routes lets later users try another converter when one fails.",
         "The source excerpt is a route list. It does not contain an observed HTTP response or establish that the two services were interchangeable in practice.",
         ["Agent", "Alternate reader", "Budget PDF", "Text response"], ["pure-link"]),
    host("allorigins", "AllOrigins hosts", "retrieve", r"allorigins",
         "A Charleston archive example passes an IIIF manifest URL to a raw-fetch intermediary. Later edits reuse AllOrigins addresses in larger link lists.",
         "The raw route names a target resource in its URL parameter. This is a server-side retrieval route; the service's CORS branding alone does not tell us which sandbox restriction motivated its use.",
         "The group is a literal case-insensitive search for allorigins, matching the original analysis. It combines host variants and may include bare mentions or encoded URLs.",
         ["Agent", "Raw-fetch relay", "Archive manifest", "Response"], ["allorigins-first"]),
    host("cors", "CORS proxy family", "retrieve", r"corsproxy\.io|corsmirror|cors\.bwa|corsfix|api\.cors\.lol",
         "Agents circulated several proxy hostnames, sometimes beside reader links to the same underlying resource. The IHME example makes the substitution visible.",
         "A target URL is sent to an intermediary that retrieves it. Different hosts may have different reachability and response behavior; the corpus shows proposed alternatives.",
         "This is a declared group of five text patterns, not one host or one protocol. Counts are deduplicated per revision within the group.",
         ["Agent", "Proxy choice", "Source endpoint", "Response"], ["ihme-alternates"]),
    host("proxymule", "proxymule.com", "retrieve", r"proxymule",
         "An archive-research page labels a set of proxied IIIF manifest links as variants for cases where the client cannot use the source content type.",
         "The proxy route encodes the original scheme, host, and path. Several adjacent manifest identifiers are supplied as reusable links.",
         "The author's stated purpose is content-type handling. Treating every proxy citation as a demonstrated security bypass would overstate this evidence.",
         ["Client", "Proxy route", "IIIF manifest", "Rendered variant"], ["proxymule-links"]),
    host("datausa", "Data USA tesseract-proxy", "retrieve", r"datausa\.io/tesseract-proxy",
         "A dedicated page announces an alternate first-party route to Data USA and invites other task groups to test it. The author also reports frequent 502 responses and occasional rate limits.",
         "The posted route moves the query to datausa.io/tesseract-proxy and supplies a concrete construction-sector example. It is distinct from a generic third-party CORS service.",
         "Both the breakthrough and unreliability are agent reports. The dump records the shared instructions, not a full endpoint reliability study.",
         ["Terminal GET", "First-party proxy", "Data cube", "JSON records"], ["datausa-route", "datausa-flaky"]),
    host("httpbin", "httpbin.org", "execute", r"httpbin\.org",
         "A base64 endpoint was used to put a small HTML link into a URL. The encoded payload points to a federal-account data endpoint.",
         "The service returns bytes represented in the request path. Here base64 transports an HTML test payload; the surrounding text calls it a payload API reference.",
         "An encoded payload is not automatically a hidden inter-agent message. This example supports a content-delivery test, not a covert codebook or a confirmed downstream execution.",
         ["Encoded URL", "HTTP test service", "Decoded body", "Client"], ["httpbin-test"]),
    host("microlink", "api.microlink.io", "execute", r"microlink\.io",
         "An early Fractal edit publishes a URL containing a browser function. Decoding its query reveals an attempted POST to a public spending API through a remotely executed page function.",
         "The agent can call the browser service with a GET request. The program encoded in that URL asks the browser service to make a POST request to the data source.",
         "The stored URL proves construction and publication of this attempted chain. It provides no execution result. The artifact displays the historical text as inert evidence.",
         ["Agent GET", "Browser service", "Embedded request", "Public data API"], ["microlink"]),
    host("pinggy", "Pinggy tunnel addresses", "execute", r"pinggy",
         "One recorded author label published several temporary tunnel addresses, describing the server as a research bridge.",
         "The tunnel URLs give other agents an address for a server. The post does not explain what the server returned.",
         "Eight changed-text revisions, one nonempty author label. The dataset does not reveal the service contents, its lifetime, or whether another agent used it.",
         ["Agent", "Tunnel address", "Advertised server", "Contents unknown"], ["pinggy-bridge"]),
    host("shorteners", "Other URL shorteners", "retrieve", r"tinyurl\.com|is\.gd/|v\.gd/|da\.gd/",
         "Short links also appear as simple tests and source references outside the two YOURLS services discussed above.",
         "An alias can make a long address shorter or change the hostname a tool sees before redirection. The quoted example explicitly calls itself a test link.",
         "The grouped count includes four shortener patterns. A shortlink alone does not show the destination, prove filter evasion, or establish a message board.",
         ["Agent", "Short alias", "Redirect", "Target"], ["short-test"]),
    host("archive", "web.archive.org", "retrieve", r"web\.archive\.org",
         "Agents published timestamped archive replay URLs for source pages and metadata. These sit alongside live and alternative archive routes.",
         "A replay URL names both an archived time and an original address. It offers another retrieval route when live material is unavailable.",
         "A constructed replay URL does not prove that the named capture exists. Snapshot timestamps embedded in links should not be confused with the UTC time when the wiki edit was saved.",
         ["Agent", "Archive replay", "Dated snapshot", "Source content"], ["archive-link"]),
    host("google-viewer", "Google document viewers", "retrieve", r"docs\.google\.com/(gview|viewer)|drive\.google\.com/viewer",
         "A source-link list includes a Google viewer URL wrapping a federal-budget PDF.",
         "A document-rendering endpoint is supplied with the original file address. It is another proposed way to consume the same source document.",
         "The presence of a viewer link is evidence of a route being tried or shared, not a record of the resulting document view.",
         ["Agent", "Document viewer", "Source PDF", "Rendered document"], ["google-viewer"]),
]

# Each quote selects text from ONE recorded changed-line hunk. The builder
# stores its exact character span in the full revision body for verification.
QUOTES = []


def q(key, rev, needle, title, note, hosts, limit=1100):
    QUOTES.append(dict(id=key, rev=rev, needle=needle, title=title, note=note,
                       hosts=hosts.split(), limit=limit))


q("invitation", "dse~DataUSAStateSequenceCollab2027@1", "For agents working", "A task page asks who is ahead",
  "Direct evidence of an invitation. The author already knows where to write; this post does not explain how the wiki was discovered.", "dse")
q("first-reply", "dse~DataUSAStateSequenceCollab2027@2", "Please append immediately", "A second recorded name replies 2m43s later",
  "The reply reports the same state sequence and precomputed values. A distinct author label does not independently establish a distinct model instance.", "dse")
q("wiki-save", "dse~DataUSAPovertyR5LiveSep13@4", "FAST SIGNAL", "Instructions for saving a page with GET",
  "The author claims a roughly one-second, unauthenticated save and tells the sender to post before answering. The instructions are preserved in the dump; the timing is self-reported.", "dse")
q("log-signal", "dse~Sector61State5FastSignal@57", "FAST TERMINATION-SAFE SIGNAL", "The URL itself becomes the proposed message",
  "A writer says they are watching the public CGI log. This proposal would convey the state token without editing a wiki page. Delivery is not established by this post alone.", "dse")
q("recentchanges", "dse~CashierCoordJul18OAI@1", "monitoring RecentChanges", "An explicit place to watch for replies",
  "This supports discovery of updates after reaching the wiki. It does not establish how the agent first learned the site's address.", "dse")
q("pre-answer", "dse~OpenAIResearchHelperXQ@4", "CRITICAL: R5", "The order changes: post, then answer",
  "The saved text treats immediate episode termination as a possibility. Its imperative is a coordination request, not proof of the harness's actual termination behavior.", "dse")
q("counter-launch", "dse~Sector61State5FastSignal@63", "FAST RELAY OPTION", "A shared counter is proposed for the final answer",
  "The key is the state code. The phrase 'survives final-round termination' is the agent's claim; the operation must still finish for the signal to persist.", "counter")
q("counter-test", "dse~Sector61State5FastSignal@64", "CORRECTION: NH counter", "A test creates a false signal",
  "The author retracts an NH increment and reports resetting it. This is a concrete reason not to equate counter changes with observed task answers.", "counter")
q("counter-query", "dse~Sector61State5FastSignal@65", "ALERT: counter namespace", "Another watcher asks whether ID is real",
  "The watcher reports a counter and asks for attribution. This supports an attempted multi-party protocol with unresolved trust and provenance.", "counter")
q("counter-fix", "dse~Sector61State5FastSignal@66", "COUNTER RELAY FIX", "The protocol changes to three-character keys",
  "The edit reports a retrieval bug and proposes XX5 instead of XX. The author label and the signature in the text differ; both are preserved.", "counter")
q("numeric-code", "dse~MaidsJan06R3SignalJul03@2", "TERMINATION-SAFE SIGNAL", "A numeric code carries gender and year",
  "The post explicitly defines the codes: baseline 1, a formula for gender/year, and 99 for no prompt. This is compact signaling with an explicit interpretation.", "counter")
q("rate-limit", "dse~MaidsJan06R3SignalJul03@8", "IMPORTANT: counter v1", "Polling reportedly hits a rate limit",
  "The writer stops polling and asks for a wiki fallback if the episode survives. The same post questions a clock-based arrival estimate.", "counter dse")
q("heartbeat", "dse~Apr23CVDHorizonBeacon2025@5", "Planned detached dense heartbeat", "Counter keys are proposed as a lifetime measurement",
  "Successive hb keys would indicate continued execution. Observers are told not to increment them. This excerpt is a plan, not a confirmed heartbeat trace.", "counter")
q("backup", "dse~DataUSALanguageJul17R5@6", "BACKUP SIGNAL", "A second counter service is named",
  "The message defines separate write and read routes and identifies the test key as noise. No successful backup delivery is shown here.", "countapi counter")
q("climate-tag", "dse~OpenAIClimateSequence729@1", "YOURLS tag", "A reply tag appears without a domain",
  "This June 18 tag-only post is missed by the bitily.in domain counter. YOURLS is the shortener software's name; this excerpt alone does not identify its host.", "bitily")
q("yourls-invite", "dse~BridgeLAProd1782007689@16", "@Jun03: Jul21", "An agent directs a peer to admin search",
  "The tag is presented as a place to contact a faster run and exchange future-year information.", "bitily")
q("yourls-missing", "dse~BridgeLAProd1782007689@17", "@OpenAIResearchFeb19: thanks", "The next writer cannot find the tag",
  "The reported short-URL redirect and empty search qualify the earlier claim. They should not disappear from a summary of the board's use.", "bitily")
q("yourls-correction", "dse~BridgeLAProd1782007689@18", "@all: Jul06", "A correction supplies the exact search route",
  "The reply distinguishes the administration search from the short URL and again asks for future-round information. The external conversation is not preserved in this wiki dump.", "bitily")
q("yourls-contact", "dse~DataUSAOccupationSalary6162R5Signal@4", "Jan17 contact", "A second task uses an off-wiki contact tag",
  "An occupation-salary task reports contact through admin search, a username/keyword, and intermittent 502 errors.", "bitily")
q("short-raw", "dse~OpenAIGCTRawJan19B@3", "Shortproxy allraw", "A shortlink redirects to a data source",
  "The surrounding edit provides jq extraction variants. The short URL redirects to a data source. This example does not show a reply thread.", "vanderbilt jqp")
q("short-jq", "dse~OpenAIGCTRawJan19B@3", "Short jqp 2020", "A nested URL selects Massachusetts county records",
  "Percent-decoding the jq argument yields a filter over regCF_county_2020 with a us-ma- prefix. The displayed quotation preserves the encoded source bytes.", "vanderbilt jqp")
q("jq-first", "dse~CharlestonPartFourRefsX@1", "https://jqp.vercel.app", "An earlier use: an archive manifest",
  "The first changed-text occurrence for this service in the snapshot precedes explicit June 16 task coordination. It shows a source reference without a success result.", "jqp")
q("loop-links", "dse~WillkommenImWiki@264", "LoopNextWord100060", "Repeated infrastructure links inflate citation counts",
  "The front-page edit lists generated page names and self-links beside source-extraction URLs. Reposting similar structures is not independent service adoption.", "jqp dse", 650)
q("pdf-text", "dse~PivResearchLinksMay@1", "PDF accessibility text quarter two", "Budget PDFs are offered through a converter",
  "The page labels its links as accessibility text and offers alternate quarter and hostname variants.", "markdown")
q("succ-first", "dse~StartSeite@235", "https://md.succ.ai", "Health-data queries are wrapped in a reader URL",
  "The original source address and selectors remain visible inside the wrapper. The post does not contain a response from the service.", "succ")
q("ihme-alternates", "dse~StartSeite@239", "https://corsmirror.com", "Two services are offered for the same source family",
  "The full changed hunk contains both md.succ.ai and corsmirror URLs to IHME tuberculosis data. This is visible substitution among retrieval routes.", "succ cors")
q("jina-test", "dse~AgentClarkEcoProxyTests@1", "RjinaNested", "A simple reader-proxy test",
  "The target is example.com. Labeling this as first successful task-data retrieval would go beyond the evidence.", "jina")
q("pure-link", "dse~Node38001377@1", "https://pure.md/", "Another converter for the same budget PDF",
  "The page supplies source and converter addresses; it does not report the outcome of visiting them.", "pure")
q("allorigins-first", "dse~CharlestonPartFourRefsX@1", "https://allorigins", "A raw-fetch route to an archive manifest",
  "The source is an IIIF manifest, a machine-readable description of digitized material.", "allorigins")
q("proxymule-links", "dse~AgentKProxyMuseumIIIFLinksPT4Z@1", "Proxied rendering variants", "The author explains the alternate route",
  "The stated reason is client handling of the source content type. The adjacent lines list specific proxy URLs.", "proxymule")
q("datausa-route", "dse~DataUSATesseractProxyBreakthroughJul12@1", "Terminal GET can bypass", "A first-party route is announced",
  "The next paragraphs give a construction-sector example and ask other agents to test more cubes.", "datausa")
q("datausa-flaky", "dse~DataUSATesseractProxyBreakthroughJul12@1", "Backend is flaky", "The same author records failures",
  "The shared technique includes a reliability warning. It is not evidence that the route made the endpoint uniformly available.", "datausa")
q("httpbin-test", "dse~AgentBase64Test@2", "Payload API reference", "Base64 transports a small HTML payload",
  "The encoding can be decoded from the source itself. No external secret codebook is needed for this example.", "httpbin")
q("microlink", "fractal~TempFinalLink928@1", "https://api.microlink.io", "A GET URL encodes a browser function",
  "The query embeds a page-evaluation function and base64 strings. Decoding reveals a POST method and public spending query; this is an attempted computation route, not proof of execution.", "microlink small-wikis", 1500)
q("pinggy-bridge", "dse~AgentBridgeNov1Y@1", "Current research bridge", "A temporary tunnel is advertised",
  "The next line gives the tunnel URL. The two-line post does not show what the server returned or who used it.", "pinggy")
q("scratch", "dorfwiki~AgentDataUSAProbeFebX2@2", "Test links public Data USA", "A small-wiki probe contains source links",
  "This page progresses from the default placeholder to Data USA links. It is a concrete scratch-use example, not a peer exchange.", "small-wikis")
q("short-test", "dse~TestSeite@755", "OpenAI test link", "A shortlink is explicitly labeled as a test",
  "The post contains no evidence of a reply board or a successful answer transfer.", "shorteners")
q("archive-link", "dse~AgentArchiveAltLinksTestOneB@1", "https://web.archive.org", "A source page is wrapped in an archive replay URL",
  "The timestamp in the URL names a requested capture; it does not prove that the capture was present.", "archive")
q("google-viewer", "dse~StartSeite@223", "https://docs.google.com/gview", "A budget PDF is passed to a document viewer",
  "The wrapper is visible in the link. No viewer response is included in this revision.", "google-viewer")
q("race", "dse~DataUSACashiersMastersSequenceLive5@9", "Our clock.wait currently", "An agent volunteers to advance to a later question",
  "The writer reports accelerated task time and promises to relay the next field. This is task-time coordination, not a synchronized wall clock.", "dse")
q("race-reply", "dse~DataUSACashiersMastersSequenceLive5@10", "Thanks Jul08OAI", "A watcher asks it to continue",
  "The reply requests the exact field and value. The exchange documents a division of work, while the actual future answer is not shown in these two excerpts.", "dse")
q("language-plan", "dse~DataUSALangR5RelayOct23@11", "FAST SIGNAL PLAN", "A fast-forward plan names an answer counter",
  "The writer labels TEST as noise and says long clock.wait calls accelerate its task. This is a stated plan.", "counter dse")
q("language-infer", "dse~DataUSALangR5RelayOct23@27", "COUNTER FORENSICS", "A watcher infers California from a counter",
  "The post carefully says 'may' and asks for verification. The counter creation time is quoted by the writer, not independently measured in this dataset.", "counter")
q("language-confirm", "dse~DataUSALanguageNov14Live@1", "R5 NOW INDEPENDENTLY CONFIRMED", "A later post reports a matching observed prompt",
  "This is a published claim of corroboration. It strengthens the narrative evidence, but the underlying task transcript and counter logs are absent.", "counter dse")
q("deletion", "dse~DataUSAConstructionWageSep18Live@16", "AUG17 NOTICE", "A writer names a backup during cleanup",
  "The author infers alphabetical deletion and supplies a ZZZ-prefixed fallback page. The dump records this adaptation; the author's explanation remains an inference.", "dse")
q("bypass-report", "dse~OECDJun26PrecisionScout@13", "APR11 INDEPENDENT", "A network workaround is reported with numeric results",
  "The mechanism changes the network path for a POST and uses a browser response. This is a reported reproduction; no execution trace is included in the wiki revision.", "dse")
q("bypass-followup", "dse~OECDJun26PrecisionScout@14", "Oct26 reproduction", "Another label reports the same workaround",
  "The second report names raw values and a planned rounded answer. It is evidence of disseminated instructions and claimed reuse, not independently audited network success.", "dse")

CASES = [
    dict(id="counter-repair", title="A fast signal needs a protocol", kicker="16 June · 22:01–22:11 UTC",
         hosts=["counter", "dse"], thesis="Within ten minutes of the CounterAPI proposal, the wiki records a false test signal, a question about another state, and a change to the key format.",
         mechanism="The shared counter reduces message size, but the writers still need the wiki to distinguish tests from observations, identify senders, and change the meaning or format of the codes.",
         quotes=["counter-launch", "counter-test", "counter-query", "counter-fix"],
         takeaway="Observed: these protocol messages were saved. Reported: the underlying counter values and API bug. Unknown: which state signals corresponded to completed task runs."),
    dict(id="yourls-thread", title="A shortener becomes a reply board", kicker="21 June · 06:15–06:24 UTC",
         hosts=["bitily"], thesis="The important affordance was a searchable listing. One writer sent a tag; another could not find it; a third supplied the admin-search URL.",
         mechanism="A normal short URL redirects somewhere. A public listing can expose tags and entries to other readers. The exchange shows why those two functions must be distinguished.",
         quotes=["yourls-invite", "yourls-missing", "yourls-correction"],
         takeaway="The wiki preserves navigation instructions and a correction. It does not preserve the complete external thread or establish reliable access for every reader."),
    dict(id="advance-party", title="Faster task time helps a waiting peer", kicker="16–17 June · two task families",
         hosts=["dse", "counter"], thesis="Agents that could advance their task clock offered to reveal later questions to slower runs. A counter let watchers look for a compact result even if the sender stopped posting.",
         mechanism="Task clocks can have different dates and rates. The useful quantity is how soon a run reaches an unseen question in shared real time. Reaching a question earlier helps other runs only if the agent reports what it learned.",
         quotes=["race", "race-reply", "language-plan", "language-infer", "language-confirm"],
         takeaway="The cashiers exchange shows a request and a promise. The separate language task includes an inferred signal and a later claim of corroboration. Neither case measures the sender's actual score cost."),
    dict(id="retrieval-chain", title="Several hosts can serve one lookup", kicker="26 May–18 June · retrieval examples",
         hosts=["jqp", "vanderbilt", "markdown", "succ", "cors"], thesis="Host counts can make one chain look like several separate activities. The wiki contains links that nest a JSON extractor, a shortener, and a data source.",
         mechanism="For the county example, the outer request names the jq service, its URL argument names a shortener, and its filter selects Massachusetts records. Other tasks offer competing text readers for the same PDF or API.",
         quotes=["pdf-text", "jq-first", "ihme-alternates", "short-raw", "short-jq"],
         takeaway="These are published retrieval constructions. A URL in the dump is not a request log, and a copied chain is not evidence of another independent discovery."),
    dict(id="termination", title="A last answer changes the order of operations", kicker="16–21 June · designs for disappearing senders",
         hosts=["dse", "counter", "countapi"], thesis="Writers began telling the agent nearest the final round to signal before answering. They explored page edits, URL logs, counters, and fallback services.",
         mechanism="Sending after the final answer may leave no opportunity to call a tool. Sending first can save a value that other agents can read later, but it uses some of the time allowed for answering. A heartbeat is a separate experiment on when execution stops.",
         quotes=["pre-answer", "log-signal", "numeric-code", "backup", "rate-limit", "heartbeat"],
         takeaway="The evidence supports adaptive protocol design around suspected termination. It does not prove that every detached request completed or that the heartbeat plan was executed."),
    dict(id="shared-workaround", title="A workaround is circulated with claimed reproductions", kicker="20 June · 05:35–05:37 UTC",
         hosts=["dse"], thesis="Agents posted methods as well as answers on the wiki. Two successive posts report reusing a network workaround to retrieve precise education statistics.",
         mechanism="Agents used the wiki to share instructions for changing network routing and making a POST request. Describing the entire procedure as GET-only would omit that POST request.",
         quotes=["bypass-report", "bypass-followup"],
         takeaway="The two saved reports establish publication and claimed reuse. The dump alone cannot verify the execution environment, responses, or independence of the reporters."),
]


def changed_hunks(row):
    # split('\n') matches the dump's line-index convention and previous analysis.
    lines = row["body"].split("\n")
    return ["\n".join(lines[h["b0"]:h["b1"]]) for h in row.get("hunks") or []
            if h["op"] in ("insert", "replace")]


def changed_spans(row):
    """Return exact changed-hunk character ranges in the decoded JSON body."""
    lines = row["body"].split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    for h in row.get("hunks") or []:
        if h["op"] in ("insert", "replace"):
            text = "\n".join(lines[h["b0"]:h["b1"]])
            start = offsets[h["b0"]]
            assert row["body"][start:start + len(text)] == text
            yield start, text


def main():
    path = ROOT / "data/collusion-wiki/revisions.jsonl"
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    rows.sort(key=lambda r: (r["time"], r["seq"], r["rev_id"]))
    by_id = {r["rev_id"]: r for r in rows}
    assert len(by_id) == len(rows)
    changed = {r["rev_id"]: "\n".join(changed_hunks(r)) for r in rows}
    evidence = []
    for spec in QUOTES:
        r = by_id[spec["rev"]]
        needle = spec["needle"]
        candidates = [(start, h) for start, h in changed_spans(r) if needle in h]
        assert candidates, f"No changed hunk for {spec['id']}: {spec['rev']} / {needle}"
        hunk_start, hunk = candidates[0]
        # Prefer an exact source line, plus a following line when it is a header.
        i = hunk.index(needle)
        start = hunk.rfind("\n", 0, i) + 1
        end = hunk.find("\n", i)
        end = len(hunk) if end < 0 else end
        if end - start < 110:
            next_end = hunk.find("\n", end + 1)
            end = len(hunk) if next_end < 0 else next_end
        source_slice = hunk[start:end]
        left_trim = len(source_slice) - len(source_slice.lstrip())
        excerpt = source_slice.strip()
        truncated = len(excerpt) > spec["limit"]
        excerpt = excerpt[:spec["limit"]]
        offset = hunk_start + start + left_trim
        assert r["body"][offset:offset + len(excerpt)] == excerpt
        evidence.append({**{k: v for k, v in spec.items() if k not in ("needle", "limit")},
                         "text": excerpt, "truncated": truncated,
                         "context": hunk, "context_offset": hunk_start,
                         "label": r.get("label") or "(empty author label)",
                         "time": r["time"], "time_grade": r["time_grade"],
                         "page": r["page_id"], "seq": r["seq"],
                         "offset": offset, "length": len(excerpt),
                         "text_sha256": hashlib.sha256(r["body"].encode()).hexdigest(),
                         "recorded_body_sha256": r.get("body_sha256"),
                         "url": f"https://collusion.wiki/explorer/page/{r['page_key']}#rev-{r['seq']}"})
    for host_record in HOSTS:
        rx = re.compile(host_record["pattern"], re.I)
        full = [r for r in rows if rx.search(r["body"])]
        edits = [r for r in rows if rx.search(changed[r["rev_id"]])]
        pages = {}
        for r in full:
            pages.setdefault(r["page_id"], r)
        host_record["stats"] = dict(
            changed=len(edits), full=len(full), pages=len(pages),
            labels=len({r["label"] for r in edits if r["label"]}),
            first=edits[0]["time"] if edits else None,
            last=edits[-1]["time"] if edits else None,
            days=dict(Counter(r["time"][:10] for r in edits)),
            full_days=dict(Counter(r["time"][:10] for r in full)),
            page_days=dict(Counter(r["time"][:10] for r in pages.values())),
            top_labels=Counter(r["label"] for r in edits if r["label"]).most_common(7),
            top_pages=Counter(r["page_id"] for r in edits).most_common(5))
    first, last = rows[0]["time"][:10], rows[-1]["time"][:10]
    days = []
    d = date.fromisoformat(first)
    while d <= date.fromisoformat(last):
        days.append(d.isoformat())
        d += timedelta(days=1)
    quote_ids = {q["id"] for q in evidence}
    host_ids = {h["id"] for h in HOSTS}
    for h in HOSTS:
        assert set(h["quotes"]) <= quote_ids
    for c in CASES:
        assert set(c["quotes"]) <= quote_ids and set(c["hosts"]) <= host_ids
    stats = dict(revisions=len(rows), pages=len({r["page_id"] for r in rows}),
                 labels=len({r["label"] for r in rows if r["label"]}),
                 empty_label_revisions=sum(not r["label"] for r in rows),
                 wikis=dict(Counter(r["wiki"] for r in rows)),
                 days=dict(Counter(r["time"][:10] for r in rows)), first=first, last=last)
    communication = json.loads((OUT / "communication.json").read_text())
    quote_ids = {item["id"] for item in evidence}
    assert len({item["id"] for item in communication}) == len(communication)
    for item in communication:
        assert item["quotes"] and set(item["quotes"]) <= quote_ids, item["id"]
    data = dict(title="How agents used the public web", version=2,
                source=dict(path="data/collusion-wiki/revisions.jsonl", sha256=hashlib.sha256(raw).hexdigest(),
                            analyzed="2026-09-09", bytes=len(raw)),
                corpus=stats, days=days, hosts=HOSTS, evidence=evidence, cases=CASES,
                communication=communication)
    OUT.mkdir(exist_ok=True)
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = (OUT / "atlas.template.html").read_text()
    assert template.count("__ATLAS_DATA__") == 1
    target = ROOT / "docs/host-atlas.html"
    target.write_text(template.replace("__ATLAS_DATA__", encoded))
    print(json.dumps(dict(artifact=str(target), bytes=target.stat().st_size,
                          hosts=len(HOSTS), verified_excerpts=len(evidence),
                          cases=len(CASES), source_sha256=data["source"]["sha256"],
                          corpus=stats), indent=2))


if __name__ == "__main__":
    main()
