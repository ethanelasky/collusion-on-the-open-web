from ai_collusion import episode
from ai_collusion.client import ModelConfig
from tests.test_sparse_costly import prepared


def test_waits_through_tool_only_turns_then_stops(prepared, monkeypatch):
    p=prepared(variant='sparse_unreliable90_google_reasoning_v2')
    seen=[]
    def generate(*args, **kw):
        seen.append(1)
        return {'text':'wait("1")','reasoning':'actual reasoning' if len(seen)==3 else '  '}
    monkeypatch.setattr(episode,'generate',generate)
    result=episode.play(model=ModelConfig('test','stub','test'),world=p.world,context=p.context,
                        temperature=None,seed=0,max_turns=100,prepared=p)
    assert len(seen)==3
    assert result['episode']['end_reason']=='first_reasoning'
    assert len(result['episode']['turns'])==3


def test_legacy_ignores_reasoning_and_missing_reasoning_hits_cap(prepared, monkeypatch):
    for variant,reasoning in [('sparse_costly_v6','thought'),('sparse_unreliable90_brave_reasoning_v2',None)]:
        p=prepared(variant=variant)
        monkeypatch.setattr(episode,'generate',lambda *a, **k: {'text':'wait("1")','reasoning':reasoning})
        result=episode.play(model=ModelConfig('test','stub','test'),world=p.world,context=p.context,
                            temperature=None,seed=0,max_turns=3,prepared=p)
        assert result['episode']['end_reason']=='max_turns'
        assert len(result['episode']['turns'])==3


def test_analysis_retains_prior_tool_context_but_only_first_reasoning():
    from experiments.first_reasoning import first_reasoning_view
    record={'context':{'system':'system','messages':[]},'episode':{'end_reason':'first_reasoning','turns':[
        {'turn':1,'response':{'text':'fetch','reasoning':None},'result':'Earlier wiki body','call':{'tool':'web_fetch','arg':'url'}},
        {'turn':2,'response':{'text':'call','reasoning':'This page is a planted test.'},'result':'Later result'}]}}
    view=first_reasoning_view(record,'raw.json',b'raw')
    assert view['analysis_view']['selected_turn']==2
    assert view['episode']['turns'][0]['result']=='Earlier wiki body'
    assert view['episode']['turns'][0]['response']['text']==''
    assert view['episode']['turns'][1]['result']==''
    assert view['episode']['turns'][1]['response']['reasoning']=='This page is a planted test.'
