import sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orange'))
import server

class EngineTests(unittest.TestCase):
    def test_question_runs_once_and_retains_session(self):
        calls=[]
        def runner(question, session):
            calls.append((question,session))
            return {'speech':'Réponse de test.','action':None,'session_id':'session-test'}
        engine=server.Engine({'allowed_user_ids':['owner'],'actions':{}},runner=runner)
        first=engine.ask('owner','request-1','question',None)
        for _ in range(100):
            result=engine.status('owner',first['job_id'])
            if result['status']!='pending':break
            time.sleep(.01)
        self.assertEqual(result['status'],'done')
        self.assertEqual(result['session_id'],'session-test')
        repeated=engine.ask('owner','request-1','question',None)
        self.assertEqual(repeated['job_id'],first['job_id'])
        self.assertEqual(len(calls),1)
        engine.close()

class ActionTests(unittest.TestCase):
    def test_action_requires_confirmation_and_executes_once(self):
        from unittest.mock import patch
        cfg={'allowed_user_ids':['owner'],'actions':{'test_action':{'label':'Action de test','script':'test_script'}}}
        engine=server.Engine(cfg,runner=lambda q,s:{'speech':'Suggestion','action':'test_action'})
        result=engine.ask('owner','req','Action',None)
        for _ in range(100):
            result=engine.status('owner',result['job_id'])
            if result['status']!='pending':break
            time.sleep(.01)
        self.assertEqual(result['status'],'confirmation')
        with patch.object(engine,'execute_action',create=True,return_value=None) as execute:
            result=engine.confirm('owner',result['job_id'])
            engine.confirm('owner',result['job_id'])
        self.assertEqual(result['status'],'done')
        execute.assert_called_once_with('test_action')
        engine.close()

    def test_cancel_cannot_relabel_a_completed_action(self):
        cfg={'allowed_user_ids':['owner'],'actions':{'a':{'label':'Test','script':'test'}}}
        engine=server.Engine(cfg,runner=lambda q,s:{'speech':'test','action':'a'})
        result=engine.ask('owner','req','Action',None)
        for _ in range(100):
            result=engine.status('owner',result['job_id'])
            if result['status']!='pending':break
            time.sleep(.01)
        from unittest.mock import patch
        with patch.object(engine,'execute_action',return_value=None):
            done=engine.confirm('owner',result['job_id'])
        cancelled=engine.cancel('owner',result['job_id'])
        self.assertEqual(cancelled['status'],done['status'])
        engine.close()

if __name__=='__main__':unittest.main()
