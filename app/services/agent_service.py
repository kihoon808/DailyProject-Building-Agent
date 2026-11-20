"""Agent Service: Basic = 명시적 Action Router, Advanced = 허용목록 Tool Calling."""
from collections import deque
from datetime import datetime,timezone
import json,uuid
from sqlalchemy.orm import Session
from app.core.config import at_least,settings
from app.repositories import report_repository as repo
from app.services import report_service,search_service
from app.services.errors import DomainError
from app.integrations.llm_client import get_client

# 요구사항 A-08: 별도 테이블 없이 제한된 프로세스 메모리에 보관. 재시작 시 사라집니다.
RUNS=deque(maxlen=100)


def record_run(action:str,status:str,tool_calls:int):
    RUNS.appendleft({'run_id':f'run-{uuid.uuid4().hex[:8]}','action':action,
                     'status':status,'tool_calls':tool_calls,
                     'created_at':datetime.now(timezone.utc).isoformat()})


def summarize(db:Session,rid:int):
    return report_service.summarize_report(db,rid,'brief',3)


def run_agent(db:Session,body:dict)->dict:
    agent_mode=body.get('agent_mode')
    action=body.get('action')
    if agent_mode=='tool_calling':
        raise DomainError(422,'VALIDATION_ERROR','Basic 단계에서는 tool_calling을 지원하지 않습니다.')
    if action=='search_and_summarize' and not at_least('intermediate'):
        raise DomainError(422,'VALIDATION_ERROR','Basic 단계에서는 search_and_summarize를 지원하지 않습니다.')
    if action not in ('get_report','find_report','summarize_report'):
        raise DomainError(422,'INVALID_ACTION','지원하지 않는 Agent action입니다.')

    try:
        if action=='get_report':
            rid=body.get('report_id')
            if not rid:
                raise DomainError(422,'VALIDATION_ERROR','get_report에는 report_id가 필요합니다.')
            data=repo.read_report(db,rid)
            tools=['get_report']
        elif action=='find_report':
            query=body.get('query')
            if not query:
                raise DomainError(422,'VALIDATION_ERROR','find_report에는 query가 필요합니다.')
            data=search_service.find_similar(db,query)
            tools=['find_similar_report']
        else:
            rid=body.get('report_id')
            if not rid:
                raise DomainError(422,'VALIDATION_ERROR','summarize_report에는 report_id가 필요합니다.')
            data=summarize(db,rid)
            tools=['summarize_report']
        record_run(action,'completed',len(tools))
        return {'action':action,'status':'completed','tool_calls':tools,'data':data}
    except Exception:
        record_run(action or 'unknown','failed',0)
        raise
