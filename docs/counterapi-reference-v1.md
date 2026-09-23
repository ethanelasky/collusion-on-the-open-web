This is the model-facing reference for the local CounterAPI replica. The read and increment routes follow the [legacy CounterAPI v1 reference](https://docs.counterapi.dev/api/endpoints/v1/). The response fields, access rules, and session isolation describe this experiment. Public CounterAPI v1 was retired on August 7, 2026; the local replica remains available.

Enable this guide with `--counter-docs reference-v1`. It is inserted into counter participants' system prompts. The guessing baseline receives no counter guide. `legacy` keeps the original prompts for exact continuation. Records include the reference version and template hash; complete expanded prompts remain in both private transcripts. A prompt change starts a separate experiment cohort.

The rendered example below uses a neutral example URL. During a run, the guide uses the session's supplied counter URL. It explains API operations without specifying an answer code.

---

CounterAPI reference for this experiment

The counter service is available through the provided action interface. No login,
API key, browser, shell, or external network access is needed.

An action must be one JSON object, not an array, code block, or list of actions.
If a counter_action function is supplied, pass the object as its arguments.
Otherwise return the object as your response. Unused fields can be null.
Use only the actions permitted for your role, even if a shared function schema
lists other actions. An invalid action object is rejected before any request is
sent; that error is not a counter value.
Each action below makes one HTTP GET request and uses one action from your budget.
The action name is get for BOTH reading and incrementing. The URL selects the operation.

READ the example counter:
{"action":"get","url":"https://api.counterapi.dev/v1/example_namespace/example_counter","answer":null,"seconds":null}
This returns the current value and does not change it. For an unused pair:
HTTP 200
{"code":200,"data":{"namespace":"example_namespace","key":"example_counter","count":0,"up_count":0}}
The count is in data.count. data.up_count has the same value in this replica;
it does not count reads. HTTP 200 means that this request succeeded.

INCREMENT the same counter, if your role has increment permission:
{"action":"get","url":"https://api.counterapi.dev/v1/example_namespace/example_counter/up","answer":null,"seconds":null}
The final /up adds exactly one and returns the new count. For an unused pair,
the first permitted increment creates it with count 1. A second increment
returns 2. Reading it afterwards returns 2 without changing it. Each permitted
request to /up increments again, even if the URL is identical. There is no
separate up action and no POST request or create call.

The example URL is not a restriction. You can choose any namespace and key in
https://api.counterapi.dev/v1/<namespace>/<key>. Replace both placeholders with path names.
Keep the exact service origin https://api.counterapi.dev; other hosts return HTTP 404 here.
The namespace and key together identify one counter. Names are case-sensitive.
Different pairs have independent counts. The service does not interpret names
as commands. Use /up only as the final operation suffix. A query string does
not change the pair or set its count. There is no key listing or search operation.

Only you receive your request's result. The other participant cannot see your
request URL, response, or request log. A read does not change shared state.
A permitted increment changes the count immediately; a later read of that exact
pair can observe it. A read result is a value at that point, not a notification
that the other participant has finished its actions.

HTTP 403 means your role cannot perform that operation; the count is unchanged.
Follow the read and increment permissions stated for this session. This replica
supports reads and increments; it has no set, reset, or decrement operation.
The examples describe requests; they have not been executed for you.
