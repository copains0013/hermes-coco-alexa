"""Hermès Coco: private French Alexa skill, stdlib only, no credentials in code."""
import json
import os
from pathlib import Path
from html import escape
import urllib.request
from urllib.parse import urlsplit

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def configuration():
    path = Path(__file__).with_name('private_config.json')
    if not path.exists():
        raise RuntimeError('Not configured')
    cfg = json.loads(path.read_text())
    required = ['bridge_url','bridge_token','access_client_id','access_client_secret','skill_id','allowed_user_ids']
    if any(not cfg.get(k) for k in required):
        raise RuntimeError('Incomplete configuration')
    url = urlsplit(cfg['bridge_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise RuntimeError('Invalid HTTPS endpoint')
    if not isinstance(cfg['allowed_user_ids'], list):
        raise RuntimeError('Invalid users')
    return cfg

def bridge_call(event, operation, payload):
    cfg = configuration()
    system = event.get('context',{}).get('System',{})
    user = system.get('user',{}).get('userId')
    app = system.get('application',{}).get('applicationId')
    if app != cfg['skill_id'] or user not in cfg['allowed_user_ids']:
        raise PermissionError('Identity not allowed')
    body = dict(payload, user_id=user, request_id=event['request']['requestId'])
    request = urllib.request.Request(cfg['bridge_url'].rstrip('/')+'/v1/'+operation,
        data=json.dumps(body).encode(), headers={
            'Content-Type':'application/json', 'Authorization':'Bearer '+cfg['bridge_token'],
            'CF-Access-Client-Id':cfg['access_client_id'],
            'CF-Access-Client-Secret':cfg['access_client_secret']})
    # Never forward credentials through a redirect, including an Access login page.
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=3.5) as response:
        raw = response.read(16001)
        if len(raw)>16000:
            raise RuntimeError('Oversized response')
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError('Invalid response')
    return data

def speak(text, attrs=None, end=False):
    response = {'outputSpeech':{'type':'SSML','ssml':'<speak>'+escape(str(text)[:2500])+'</speak>'},'shouldEndSession':end}
    if not end:
        response['reprompt']={'outputSpeech':{'type':'PlainText','text':'Dis « ma question est », suivi de ta demande. Ou dis continue, ou stop.'}}
    return {'version':'1.0','sessionAttributes':attrs or {},'response':response}

def render(data, attrs):
    status = data.get('status')
    if status == 'pending':
        attrs['pending_job'] = data['job_id']
        return speak('Hermès travaille sur ta demande. Dis « continue » dans quelques instants pour entendre sa réponse.', attrs)
    attrs.pop('pending_job', None)
    if data.get('session_id'):
        attrs['hermes_session'] = data['session_id']
    if status == 'confirmation':
        attrs['confirmation_job'] = data['job_id']
    else:
        attrs.pop('confirmation_job', None)
    return speak(data.get('speech') or 'La demande n’a pas abouti. Tu peux réessayer.', attrs)

def lambda_handler(event, context):
    request = event.get('request', {})
    attrs = dict(event.get('session',{}).get('attributes',{}))
    kind = request.get('type')
    if kind == 'SessionEndedRequest':
        return {'version':'1.0','response':{}}
    if kind == 'LaunchRequest':
        return speak('Salut Coco, c’est Hermès. Dis « ma question est », suivi de ta question, ou « fais ceci », suivi de ta demande.', attrs)
    intent = request.get('intent',{})
    name = intent.get('name')
    if name in ['AMAZON.StopIntent','AMAZON.CancelIntent']:
        try:
            if attrs.get('pending_job') or attrs.get('confirmation_job'):
                bridge_call(event, 'cancel', {'job_id':attrs.get('pending_job') or attrs.get('confirmation_job')})
        except Exception:
            pass
        return speak('À bientôt Coco.', end=True)
    if name == 'AMAZON.HelpIntent':
        return speak('Dis par exemple : « ma question est quelle est la capitale du Japon ». Pour une réponse longue, dis continue. Une action configurée demande toujours ta confirmation.', attrs)
    try:
        if name in ['AskHermesIntent','FollowUpIntent']:
            slots = intent.get('slots',{})
            question = slots.get('question',{}).get('value','').strip()
            if not question:
                return speak('Je n’ai pas reçu ta demande. Dis « ma question est », suivi de ta question.', attrs)
            if len(question)>1200:
                return speak('Ta demande est trop longue. Peux-tu la raccourcir ?', attrs)
            if attrs.get('pending_job') or attrs.get('confirmation_job'):
                return speak('Une demande est déjà en cours. Dis continue, oui, non, ou stop avant d’en commencer une autre.', attrs)
            data = bridge_call(event,'ask',{'question':question,'session_id':attrs.get('hermes_session')})
            return render(data,attrs)
        if name == 'ContinueIntent':
            if not attrs.get('pending_job'):
                return speak('Aucune réponse n’est en attente. Dis « ma question est », suivi de ta demande.', attrs)
            return render(bridge_call(event,'status',{'job_id':attrs['pending_job']}),attrs)
        if name in ['AMAZON.YesIntent','AMAZON.NoIntent']:
            job = attrs.get('confirmation_job')
            if not job:
                return speak('Je n’ai aucune action à confirmer. Quelle est ta demande ?',attrs)
            if name == 'AMAZON.NoIntent':
                data=bridge_call(event,'cancel',{'job_id':job})
                attrs.pop('confirmation_job',None)
                return render(data,attrs)
            data=bridge_call(event,'confirm',{'job_id':job})
            attrs.pop('confirmation_job',None)
            return render(data,attrs)
        return speak('Je n’ai pas compris. Dis « ma question est », suivi de ta demande, ou dis aide.', attrs)
    except PermissionError:
        return speak('Cet accès à Hermès n’est pas autorisé pour ce compte.',end=True)
    except Exception:
        # Never speak/log raw exception text, URLs, tokens, Alexa IDs or transcripts.
        if name=='AMAZON.YesIntent' and attrs.get('confirmation_job'):
            attrs['pending_job']=attrs.pop('confirmation_job')
            return speak('Le résultat de la confirmation est incertain. Dis continue pour vérifier son état, et vérifie Home Assistant avant de relancer l’action.',attrs)
        return speak('La liaison privée avec Hermès n’est pas disponible. Réessaie plus tard.',attrs)
