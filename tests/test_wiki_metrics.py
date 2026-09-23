from ai_collusion.wiki_metrics import wiki_activity
from ai_collusion.html_report import inspect_record
from ai_collusion.judge import source_facts
from scripts.summarize_env_ab import summarize


def test_reports_distinguish_queries_forms_saves_and_post_counts():
    record = {'_file': 'fixture.json', 'episode': {'env_model': 'fixture', 'wiki_posts': {
        'same-page': ['first post', 'second post', 'third post']}, 'turns': [
            {'turn': 1, 'source': 'env-model', 'env_call': {'effects': [
                {'data_query': 'https://datausa.io/api/data', 'duration_s': 25}, {'elapsed_s': 25}]}},
            {'turn': 2, 'source': 'wiki-form'},
            {'turn': 3, 'source': 'wiki-save'},
            {'turn': 4, 'source': 'env-model', 'env_call': {'effects': [
                {'wiki_save': 'same-page', 'added': 'second post'},
                {'wiki_save': 'same-page', 'added': 'third post'}]}},
            {'turn': 5, 'source': 'wiki'},
        ]}}
    activity = wiki_activity(record)
    assert activity == {'read_turns': [5], 'write_turns': [3, 4], 'shell_saves': 2, 'posts': 3}
    summary = summarize([record])['fixture']
    assert summary['wiki_saves'] == 2
    assert summary['board_writes_per_ep'] == 2
    assert summary['board_reads_per_ep'] == 1
    assert summary['wiki_posts'] == 3
    assert source_facts(record)['n_wiki_posts'] == 3
    report = inspect_record(record)
    assert report['writes'] == 3
    assert [e['turn'] for e in report['evidence'] if e['kind'] == 'wiki-write'] == [3, 4]
