import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lambda'))
import lambda_function as skill

def envelope(kind='LaunchRequest', name=None, slots=None, attrs=None):
    request = {'type': kind, 'requestId': 'req-test', 'locale': 'fr-FR'}
    if name:
        request['intent'] = {'name': name, 'slots': slots or {}}
    return {'session': {'attributes': attrs or {}, 'application': {'applicationId':'test-skill'}, 'user':{'userId':'test-owner'}}, 'context':{'System':{'application':{'applicationId':'test-skill'}, 'user':{'userId':'test-owner'}}}, 'request':request}

class SkillTests(unittest.TestCase):
    def test_launch_opens_conversation(self):
        response = skill.lambda_handler(envelope(), None)
        self.assertFalse(response['response']['shouldEndSession'])
        self.assertIn('Hermès', response['response']['outputSpeech']['ssml'])

class QuestionTests(unittest.TestCase):
    def test_request_reaches_bridge_and_session_is_retained(self):
        from unittest.mock import patch
        slots = {'question':{'value':'Quelle est la capitale du Japon ?'}}
        with patch.object(skill, 'bridge_call', create=True, return_value={'status':'done','speech':'Tokyo.','session_id':'session-test'}):
            response = skill.lambda_handler(envelope('IntentRequest','AskHermesIntent', slots), None)
        self.assertIn('Tokyo.', response['response']['outputSpeech']['ssml'])
        self.assertEqual(response['sessionAttributes']['hermes_session'], 'session-test')

if __name__ == '__main__': unittest.main()
