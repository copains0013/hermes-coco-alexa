"""Loopback-only authenticated bridge. No public listener, shell or transcript logs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from zoneinfo import ZoneInfo
import urllib.request

class Engine:
    def __init__(self,cfg,runner=None):
        self.cfg=cfg
        self.runner=runner or self.hermes
        self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=1)
        self.jobs={}
        self.requests={}
        self.sessions={}
    def close(self):
        self.pool.shutdown(wait=True)
    def owner(self,owner):
        if owner not in self.cfg.get('allowed_user_ids',[]):
            raise PermissionError('Unknown owner')
    def ask(self,owner,request_id,question,session):
        self.owner(owner)
        if not isinstance(question,str) or not 1<=len(question.strip())<=1200:
            raise ValueError('Invalid question')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=200:
            raise ValueError('Invalid request')
        with self.lock:
            now=time.monotonic()
            # Completed jobs expire in memory. No durable transcript database here.
            for key in list(self.jobs):
                if now-self.jobs[key]['created']>900 and self.jobs[key]['status'] not in ['pending','executing']:
                    self.jobs.pop(key)
            self.requests={key:job for key,job in self.requests.items() if job in self.jobs}
            if (owner,request_id) in self.requests:
                return self.status(owner,self.requests[owner,request_id])
            if session and self.sessions.get(session)!=owner:
                raise PermissionError('Session ownership mismatch')
            if sum(j['status']=='pending' for j in self.jobs.values())>=4 or len(self.jobs)>=64:
                raise RuntimeError('Busy')
            key=uuid.uuid4().hex
            self.jobs[key]={'owner':owner,'status':'pending','created':now,'job_id':key}
            self.requests[owner,request_id]=key
            self.pool.submit(self.work,key,question,session)
            return {'status':'pending','job_id':key}
    def work(self,key,question,session):
        try:
            result=self.runner(question,session)
            if not isinstance(result,dict) or not isinstance(result.get('speech'),str) or not result['speech'].strip():
                raise ValueError('Invalid agent output')
            with self.lock:
                job=self.jobs[key]
                if job['status']!='pending':return
                job.update(status='done',speech=result['speech'][:2000])
                sid=result.get('session_id')
                if sid and re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}',sid):
                    self.sessions[sid]=job['owner'];job['session_id']=sid
                action=result.get('action')
                if action:
                    spec=self.cfg.get('actions',{}).get(action) if isinstance(action,str) else None
                    if not spec:
                        job.update(status='failed',speech='Cette action n’est pas autorisée. Rien n’a été exécuté.')
                    else:
                        job.update(status='confirmation',action=action,
                                   speech='Confirme : '+spec['label']+'. Dis oui pour exécuter, ou non pour annuler.')
        except Exception:
            with self.lock:
                if self.jobs[key]['status']=='pending':
                    self.jobs[key].update(status='failed',speech='Hermès n’a pas pu terminer cette demande. Aucune action n’est confirmée.')
    def status(self,owner,key):
        self.owner(owner)
        with self.lock:
            job=self.jobs.get(key)
            if not job or job['owner']!=owner or time.monotonic()-job['created']>900:
                raise PermissionError('Unknown or expired job')
            return {k:v for k,v in job.items() if k in ['status','job_id','speech','session_id']}
    def cancel(self,owner,key):
        with self.lock:
            self.status(owner,key)
            if self.jobs[key]['status'] in ['pending','confirmation']:
                self.jobs[key].update(status='cancelled',speech='Demande annulée. Aucune action n’a été lancée.')
            return self.status(owner,key)
    def confirm(self,owner,key):
        with self.lock:
            self.status(owner,key)
            job=self.jobs[key]
            if job['status']!='confirmation':return self.status(owner,key)
            if time.monotonic()-job['created']>120:
                job.update(status='cancelled',speech='La confirmation a expiré. Rien n’a été exécuté.')
                return self.status(owner,key)
            job['status']='executing'
            try:
                self.execute_action(job['action'])
                job.update(status='done',speech='Home Assistant a accepté le lancement : '+self.cfg['actions'][job['action']]['label']+'.')
            except Exception:
                # Network failure after dispatch does not establish failure of the action.
                job.update(status='uncertain',speech='Je ne peux pas confirmer le résultat. Vérifie Home Assistant avant de relancer cette action.')
            return self.status(owner,key)
    def execute_action(self,action):
        spec=self.cfg.get('actions',{}).get(action,{})
        script=spec.get('script','')
        if not re.fullmatch(r'[a-z0-9_]{1,100}',script):raise ValueError('Invalid script')
        token=Path(self.cfg['ha_token_file']).read_text().strip()
        request=urllib.request.Request('http://127.0.0.1:8123/api/services/script/'+script,
            data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=2) as response:
            if response.status!=200:raise RuntimeError('HA service failed')
            response.read(8192)
    def hermes(self,question,session):
        actions={key:action['label'] for key,action in self.cfg.get('actions',{}).items()}
        clock=datetime.now(ZoneInfo(self.cfg.get('timezone','Europe/Zurich'))).isoformat()
        prompt=('Interface vocale privée de Coco. Réponds en français, deux phrases maximum. '
                'Tu n’as PAS exécuté d’action. Ne prétends jamais avoir agi. '
                'Pour une action non disponible, explique la limite. Pose une seule question si nécessaire. '
                'Réponds uniquement par un objet JSON {"speech":"réponse", "action":null}, '
                'ou action égal à une clé de la liste autorisée si la demande est explicite et complète. '
                'Aucune autre action. Liste autorisée : '+json.dumps(actions,ensure_ascii=False)+
                '. Heure locale mesurée par le serveur : '+clock+'. Demande : '+question)
        args=['hermes','chat','-Q','--toolsets','web','--max-turns','5','--ignore-rules',
              '--run-budget','75','--source','alexa-skill','--query-file','-']
        if session:args+=['--resume',session]
        proc=subprocess.run(args,input=prompt,text=True,capture_output=True,timeout=100,
                            cwd=Path(__file__).parent)
        if proc.returncode:raise RuntimeError('Agent failed')
        text=proc.stdout.strip()
        if text.startswith('```'):
            text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
        data=json.loads(text)
        match=re.search(r'session_id:\s*([A-Za-z0-9_.:-]+)',proc.stderr)
        if match:data['session_id']=match[1]
        return data

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,data):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        self.send(405,{'error':'POST required'})
    def do_POST(self):
        self.connection.settimeout(5)
        token=self.server.token
        supplied=self.headers.get('Authorization','')
        if not hmac.compare_digest(supplied,'Bearer '+token):
            self.send(401,{'error':'Unauthorized'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=8192:raise ValueError('Size')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Object')
            owner=data.get('user_id')
            engine=self.server.engine
            if self.path=='/v1/ask':
                result=engine.ask(owner,data.get('request_id'),data.get('question'),data.get('session_id'))
            elif self.path=='/v1/status':result=engine.status(owner,data.get('job_id'))
            elif self.path=='/v1/cancel':result=engine.cancel(owner,data.get('job_id'))
            elif self.path=='/v1/confirm':result=engine.confirm(owner,data.get('job_id'))
            else:self.send(404,{'error':'Unknown route'});return
            self.send(200,result)
        except PermissionError:self.send(403,{'error':'Forbidden'})
        except (ValueError,TypeError):self.send(400,{'error':'Invalid request'})
        except Exception:self.send(503,{'error':'Unavailable'})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    path=Path(args.config)
    if path.stat().st_mode & 0o077:raise SystemExit('Config must have mode 600')
    cfg=json.loads(path.read_text())
    token_path=Path(cfg['bridge_token_file'])
    if token_path.stat().st_mode & 0o077:raise SystemExit('Token must have mode 600')
    token=token_path.read_text().strip()
    if len(token)<32 or not cfg.get('allowed_user_ids'):raise SystemExit('Private setup incomplete')
    engine=Engine(cfg)
    http=ThreadingHTTPServer(('127.0.0.1',int(cfg.get('port',18764))),Handler)
    http.engine=engine;http.token=token
    print('Hermes Alexa bridge ready on loopback.',flush=True)
    try:http.serve_forever()
    finally:http.server_close();engine.close()

if __name__=='__main__':main()
