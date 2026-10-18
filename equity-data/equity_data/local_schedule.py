"""Generate owned Windows Task Scheduler XML; civil schedule remains configurable."""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from .cli import load_config
from .local import write_json


def spec(config,user_id,python=None,now=None):
    base=Path(config['_base_dir']).resolve();settings=config['local_schedule']
    zone=ZoneInfo(settings.get('timezone','Asia/Shanghai'))
    now=now or datetime.now(zone);now=now.astimezone(zone)
    from .local_control import schedule_slots
    schedule_slots(settings['times'],str(zone),now-timedelta(days=1),now)
    pulse_minutes=settings.get('pulse_minutes',5)
    if not isinstance(pulse_minutes,int) or not 1<=pulse_minutes<=60:raise ValueError('pulse_minutes must be an integer from 1 to 60')
    identity=hashlib.sha256((str(base).lower()+'|'+str(Path(config['storage']).resolve()).lower()).encode()).hexdigest()[:12]
    name='EquityData-'+identity+'-Collector';marker='EquityDataLocalV4:'+identity
    executable=Path(python or base/'.venv'/'Scripts'/'pythonw.exe').resolve()
    if not executable.is_file():raise FileNotFoundError('Fixed Python runtime missing: '+str(executable))
    ns='http://schemas.microsoft.com/windows/2004/02/mit/task';ET.register_namespace('',ns)
    def node(parent,name,text=None,**attrs):
        element=ET.SubElement(parent,'{'+ns+'}'+name,attrs)
        if text is not None:element.text=str(text)
        return element
    task=ET.Element('{'+ns+'}Task',{'version':'1.4'})
    registration=node(task,'RegistrationInfo');node(registration,'Description',marker);node(registration,'Author',user_id)
    triggers=node(task,'Triggers')
    boundaries=[]
    for index,clock in enumerate(settings['times']):
        hour,minute=map(int,clock.split(':'));start=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        trigger=node(triggers,'CalendarTrigger',id='Daily'+str(index+1))
        node(trigger,'StartBoundary',start.isoformat());node(trigger,'Enabled','true')
        node(node(trigger,'ScheduleByDay'),'DaysInterval','1');boundaries.append(start.isoformat())
    login=node(triggers,'LogonTrigger',id='LoginCatchUp');node(login,'Enabled','true');node(login,'UserId',user_id);node(login,'Delay','PT30S')
    pulse=node(triggers,'TimeTrigger',id='CatchUpDispatcher')
    repetition=node(pulse,'Repetition');node(repetition,'Interval','PT'+str(settings.get('pulse_minutes',5))+'M');node(repetition,'StopAtDurationEnd','false')
    node(pulse,'StartBoundary',(now+timedelta(minutes=1)).replace(second=0,microsecond=0).isoformat());node(pulse,'Enabled','true')
    principals=node(task,'Principals');principal=node(principals,'Principal',id='Author')
    node(principal,'UserId',user_id);node(principal,'LogonType','InteractiveToken');node(principal,'RunLevel','LeastPrivilege')
    options=node(task,'Settings')
    for key,value in {'MultipleInstancesPolicy':'IgnoreNew','DisallowStartIfOnBatteries':'false','StopIfGoingOnBatteries':'false',
        'AllowHardTerminate':'true','StartWhenAvailable':'true','RunOnlyIfNetworkAvailable':'false',
        'AllowStartOnDemand':'true','Enabled':'true','Hidden':'false','RunOnlyIfIdle':'false','WakeToRun':'false',
        'ExecutionTimeLimit':'PT'+str(max(20,int(config.get('budget',{}).get('max_runtime_seconds',600)/60)+10))+'M','Priority':'7'}.items():node(options,key,value)
    actions=node(task,'Actions',Context='Author');action=node(actions,'Exec')
    arguments='-m equity_data.local --config "'+str(Path(config['_config_path']).resolve())+'" tick'
    node(action,'Command',str(executable));node(action,'Arguments',arguments);node(action,'WorkingDirectory',str(base))
    xml=ET.tostring(task,encoding='unicode',xml_declaration=False)
    return {'task_name':name,'task_path':'\\','ownership_marker':marker,'user_id':user_id,'timezone':str(zone),
        'times':settings['times'],'calendar_start_boundaries':boundaries,'pulse_minutes':settings.get('pulse_minutes',5),
        'python':str(executable),'arguments':arguments,'working_directory':str(base),'config':config['_config_path'],
        'storage':config['storage'],'xml':xml,'logon_type':'InteractiveToken','requires_logged_on_user':True,
        'wake_from_sleep':False,'pulse_semantics':'Checks due slots and persisted work; does not unconditionally collect every pulse.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--user-id',required=True);p.add_argument('--python')
    a=p.parse_args();config=load_config(a.config);result=spec(config,a.user_id,a.python)
    dest=Path(config['storage'])/'deployment';dest.mkdir(parents=True,exist_ok=True)
    (dest/'scheduled-task.xml').write_text(result['xml'],encoding='utf-8')
    result['xml_path']=str(dest/'scheduled-task.xml');result.pop('xml')
    write_json(dest/'task-spec.json',result);print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
