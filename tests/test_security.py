"""Synthetic unit inputs + real loopback HTTP probes; no home actions or model calls."""
import json
import sys
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'lambda'),str(ROOT/'orange')]
import lambda_function as skill
import server
from test_skill import envelope

class SkillSecurityTests(unittest.TestCase):
    def test_ssml_is_escaped(self):
        self.assertIn('&lt;audio',skill.speak('<audio src="evil"/>')['response']['outputSpeech']['ssml'])
    def test_empty_query_does_not_contact_bridge(self):
        with patch.object(skill,'bridge_call') as call:
            result=skill.lambda_handler(envelope('IntentRequest','AskHermesIntent'),None)
        call.assert_not_called()
        self.assertIn('pas reçu',result['response']['outputSpeech']['ssml'])
    def test_pending_uses_continue(self):
        with patch.object(skill,'bridge_call',return_value={'status':'pending','job_id':'job-test'}):
            result=skill.lambda_handler(envelope('IntentRequest','AskHermesIntent',{'question':{'value':'question'}}),None)
        self.assertEqual(result['sessionAttributes']['pending_job'],'job-test')
    def test_confirmation_kept_in_session(self):
        with patch.object(skill,'bridge_call',return_value={'status':'confirmation','job_id':'job-test','speech':'Confirme.'}):
            result=skill.lambda_handler(envelope('IntentRequest','ContinueIntent',attrs={'pending_job':'job-test'}),None)
        self.assertEqual(result['sessionAttributes']['confirmation_job'],'job-test')
        self.assertNotIn('pending_job',result['sessionAttributes'])
    def test_error_never_speaks_exception_secret(self):
        with patch.object(skill,'bridge_call',side_effect=RuntimeError('SECRET-TEST-ONLY')):
            result=skill.lambda_handler(envelope('IntentRequest','AskHermesIntent',{'question':{'value':'question'}}),None)
        self.assertNotIn('SECRET-TEST-ONLY',json.dumps(result))
    def test_unknown_owner_never_sends_network_request(self):
        cfg={'bridge_url':'https://example.com','bridge_token':'TEST-ONLY','access_client_id':'TEST','access_client_secret':'TEST','skill_id':'test-skill','allowed_user_ids':['other']}
        with patch.object(skill,'configuration',return_value=cfg), patch.object(urllib.request,'build_opener') as opener:
            with self.assertRaises(PermissionError):skill.bridge_call(envelope(),'ask',{})
        opener.assert_not_called()
    def test_redirects_are_not_followed(self):
        self.assertIsNone(skill.NoRedirect().redirect_request(None,None,302,'Redirect',{},'https://evil.example'))
    def test_no_does_not_claim_uncertain_action_cancelled(self):
        with patch.object(skill,'bridge_call',return_value={'status':'uncertain','speech':'Résultat incertain.','job_id':'job-test'}):
            result=skill.lambda_handler(envelope('IntentRequest','AMAZON.NoIntent',attrs={'confirmation_job':'job-test'}),None)
        self.assertIn('incertain',result['response']['outputSpeech']['ssml'])
    def test_stop_closes_session(self):
        result=skill.lambda_handler(envelope('IntentRequest','AMAZON.StopIntent'),None)
        self.assertTrue(result['response']['shouldEndSession'])

class BridgeSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine=server.Engine({'allowed_user_ids':['owner','other'],'actions':{}},runner=lambda q,s:{'speech':'Test answer','action':None,'session_id':'session-test'})
    def tearDown(self):self.engine.close()
    def await_result(self,result):
        for _ in range(100):
            result=self.engine.status('owner',result['job_id'])
            if result['status']!='pending':return result
            time.sleep(.01)
        self.fail('Job timeout')
    def test_job_is_private_to_owner(self):
        result=self.engine.ask('owner','req','Question',None)
        with self.assertRaises(PermissionError):self.engine.status('other',result['job_id'])
    def test_session_is_private_to_owner(self):
        result=self.await_result(self.engine.ask('owner','req','Question',None))
        with self.assertRaises(PermissionError):self.engine.ask('other','req-other','Follow-up',result['session_id'])
    def test_oversized_query_rejected(self):
        with self.assertRaises(ValueError):self.engine.ask('owner','req','x'*1201,None)
    def test_unapproved_action_never_executes(self):
        self.engine.runner=lambda q,s:{'speech':'Suggestion','action':'unknown'}
        result=self.await_result(self.engine.ask('owner','req','Action',None))
        self.assertEqual(result['status'],'failed')
    def test_expired_confirmation_never_executes(self):
        self.engine.cfg['actions']={'a':{'label':'Test','script':'test'}}
        self.engine.runner=lambda q,s:{'speech':'Suggestion','action':'a'}
        result=self.await_result(self.engine.ask('owner','req','Action',None))
        self.engine.jobs[result['job_id']]['created']-=121
        with patch.object(self.engine,'execute_action') as execute:
            result=self.engine.confirm('owner',result['job_id'])
        execute.assert_not_called()
        self.assertEqual(result['status'],'cancelled')
    def test_uncertain_execution_never_retries(self):
        self.engine.cfg['actions']={'a':{'label':'Test','script':'test'}}
        self.engine.runner=lambda q,s:{'speech':'Suggestion','action':'a'}
        result=self.await_result(self.engine.ask('owner','req','Action',None))
        with patch.object(self.engine,'execute_action',side_effect=TimeoutError) as execute:
            result=self.engine.confirm('owner',result['job_id'])
            self.engine.confirm('owner',result['job_id'])
        self.assertEqual(result['status'],'uncertain')
        self.assertEqual(execute.call_count,1)
    def test_real_http_rejects_missing_token(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        http.engine=self.engine;http.token='TEST-ONLY-NOT-A-REAL-CREDENTIAL'
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            req=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+'/v1/ask',data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req,timeout=2)
            self.assertEqual(caught.exception.code,401)
            caught.exception.close()
        finally:http.shutdown();http.server_close();thread.join()
    def test_real_http_accepts_authorized_job(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        http.engine=self.engine;http.token='TEST-ONLY-NOT-A-REAL-CREDENTIAL'
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            body=json.dumps({'user_id':'owner','request_id':'http-test','question':'Question'}).encode()
            req=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+'/v1/ask',data=body,headers={'Authorization':'Bearer '+http.token})
            with urllib.request.urlopen(req,timeout=2) as response:result=json.load(response)
            self.assertIn('job_id',result)
            self.assertEqual(self.await_result(result)['status'],'done')
        finally:http.shutdown();http.server_close();thread.join()

class PackageTests(unittest.TestCase):
    def test_import_tree_and_french_carrier_phrases(self):
        for path in ['lambda/lambda_function.py','lambda/requirements.txt','skill-package/skill.json']:
            self.assertTrue((ROOT/path).is_file())
        model=json.loads((ROOT/'skill-package/interactionModels/custom/fr-FR.json').read_text())['interactionModel']['languageModel']
        self.assertEqual(model['invocationName'],'hermès coco')
        for intent in model['intents']:
            for sample in intent.get('samples',[]):
                if '{question}' in sample:
                    self.assertTrue(sample.replace('{question}','').strip())
        self.assertFalse((ROOT/'lambda/private_config.json').exists())

if __name__=='__main__':unittest.main()
