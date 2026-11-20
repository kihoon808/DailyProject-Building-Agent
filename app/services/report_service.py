"""Report Service: 6종 보고서 공통 CRUD/버전/동기화. Repository는 DB, Service는 업무 규칙."""
from datetime import datetime,date,timezone
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.core.config import settings,at_least
from app.models.report import (Report,ReportCategory,ReportEmbedding,PromptVersion,
                               DETAIL_MODELS, DETAIL_FIELDS, PromptReport)
from app.repositories import report_repository as repo
from app.integrations import embedding_client as emb
from app.integrations import llm_client
from app.services.errors import DomainError
from app.schemas.report import ReportCreate,ReportUpdate,PromptVersionCreate,DETAIL_SCHEMAS
from pydantic import ValidationError

INPUT_FIELDS={
    'EXPERIMENT':{'experiment_date','hypothesis','result_summary'},
    'DAILY_WORK':{'work_date','progress','blockers'},
    'RESEARCH_PLAN':{'research_goal','methodology','target_date'},
    'DATA_PROCESSING':{'dataset_name','processing_steps','quality_notes'},
    'PROMPT':{'purpose','prompt_text'},
    'MEETING':{'meeting_at','attendees','decisions'},
}
OPTIONAL={'DAILY_WORK':{'blockers'},'DATA_PROCESSING':{'quality_notes'}}
DATE_FIELDS={'experiment_date','work_date','target_date'}
TIME_FIELDS={'meeting_at'}


def validate_details(code:str,details:dict,content:str|None=None)->dict:
    schema=DETAIL_SCHEMAS.get(code)
    if schema is None or not isinstance(details,dict):
        raise DomainError(422,'VALIDATION_ERROR','보고서 카테고리 또는 details 형식이 올바르지 않습니다.')
    try:
        parsed=schema.model_validate(details).model_dump()
    except ValidationError:
        raise DomainError(422,'VALIDATION_ERROR','카테고리별 details 필드를 확인하세요.')
    if code=='PROMPT' and content is not None:
        if parsed['prompt_text']!=content:
            raise DomainError(422,'VALIDATION_ERROR','PROMPT 보고서의 content와 details.prompt_text는 일치해야 합니다.')
    return parsed


def encode_overview(overview:str)->list[float]:
    try:
        return emb.encode([overview],kind='passage')[0]
    except DomainError:
        raise
    except Exception:
        raise DomainError(503,'EMBEDDING_UNAVAILABLE','Embedding 모델 호출에 실패했습니다.')


def categories(db:Session)->dict:
    rows=db.execute(select(ReportCategory).order_by(ReportCategory.category_id.asc())).scalars().all()
    return {'items':[
        {'category_id':r.category_id,'code':r.code,'name':r.name,'description':r.description}
        for r in rows
    ]}


def create_report(db:Session,data:ReportCreate)->dict:
    category=repo.category_for_code(db,data.category_code)
    parsed_details=validate_details(category.code,data.details,content=data.content)
    vector=encode_overview(data.overview)
    now=datetime.now(timezone.utc)
    try:
        report=Report(
            category_id=category.category_id,
            overview=data.overview,
            index_status='ready',
            revision=1,
        )
        db.add(report)
        db.flush()

        detail_cls=DETAIL_MODELS[category.code]
        detail_kwargs={
            'report_id':report.report_id,
            'title':data.title,
            'content':data.content,
            'author':data.author,
            'create_at':now,
            'update_at':now,
        }
        if category.code=='PROMPT':
            detail_kwargs['purpose']=parsed_details['purpose']
            detail_kwargs['active_version_no']=1
        else:
            for k,v in parsed_details.items():
                detail_kwargs[k]=v
        db.add(detail_cls(**detail_kwargs))

        if category.code=='PROMPT':
            db.flush()
            db.add(PromptVersion(
                report_id=report.report_id,
                version_no=1,
                prompt_text=parsed_details['prompt_text'],
                created_at=now,
            ))

        db.add(ReportEmbedding(
            report_id=report.report_id,
            embedding=vector,
            model_name=settings.embedding_model,
            overview_hash=emb.overview_hash(data.overview),
            indexed_revision=1,
            indexed_at=now,
        ))
        db.commit()
    except DomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {
        'report_id':report.report_id,
        'category_code':category.code,
        'title':data.title,
        'author':data.author,
        'revision':report.revision,
        'index_status':report.index_status,
        'embedding_model':settings.embedding_model,
    }


def get_report(db:Session,rid:int)->dict:
    return repo.read_report(db,rid)


def list_reports(db:Session,title:str|None,limit:int,offset:int,
                 category_code:str|None=None,updated_after:datetime|None=None):
    return repo.list_reports(db,title,limit,offset,category_code,updated_after)


def update_report(db:Session,rid:int,data:ReportUpdate)->dict:
    report,category,detail=repo.get_report_and_detail(db,rid)
    if data.expected_revision is not None and not at_least('advanced'):
        raise DomainError(422,'VALIDATION_ERROR','Basic 단계에서는 expected_revision을 지원하지 않습니다.')
    parsed_details=validate_details(category.code,data.details,content=data.content)
    if category.code=='PROMPT' and data.content!=detail.content:
        raise DomainError(422,'VALIDATION_ERROR','PROMPT 보고서의 본문은 일반 수정으로 변경할 수 없습니다.')

    existing_emb=db.get(ReportEmbedding,rid)
    new_hash=emb.overview_hash(data.overview)
    can_reuse=(
        existing_emb is not None
        and report.index_status=='ready'
        and existing_emb.model_name==settings.embedding_model
        and existing_emb.overview_hash==new_hash
        and existing_emb.indexed_revision==report.revision
    )
    new_vector=None if can_reuse else encode_overview(data.overview)
    now=datetime.now(timezone.utc)

    try:
        report,category,detail=repo.get_report_and_detail(db,rid,lock=True)
        if data.expected_revision is not None and report.revision!=data.expected_revision:
            raise DomainError(409,'REVISION_CONFLICT','보고서 버전이 충돌했습니다.')
        if category.code=='PROMPT' and data.content!=detail.content:
            raise DomainError(422,'VALIDATION_ERROR','PROMPT 보고서의 본문은 일반 수정으로 변경할 수 없습니다.')

        existing_emb=db.get(ReportEmbedding,rid)
        still_reusable=(
            existing_emb is not None
            and report.index_status=='ready'
            and existing_emb.model_name==settings.embedding_model
            and existing_emb.overview_hash==new_hash
            and existing_emb.indexed_revision==report.revision
        )
        if not still_reusable and new_vector is None:
            new_vector=encode_overview(data.overview)

        detail.title=data.title
        detail.content=data.content
        detail.author=data.author
        detail.update_at=now
        if category.code=='PROMPT':
            detail.purpose=parsed_details['purpose']
        else:
            for k,v in parsed_details.items():
                setattr(detail,k,v)

        report.overview=data.overview
        report.revision+=1
        report.index_status='ready'

        embedding_updated=False
        if still_reusable:
            existing_emb.indexed_revision=report.revision
        else:
            embedding_updated=True
            if existing_emb is None:
                db.add(ReportEmbedding(
                    report_id=rid,
                    embedding=new_vector,
                    model_name=settings.embedding_model,
                    overview_hash=new_hash,
                    indexed_revision=report.revision,
                    indexed_at=now,
                ))
            else:
                existing_emb.embedding=new_vector
                existing_emb.model_name=settings.embedding_model
                existing_emb.overview_hash=new_hash
                existing_emb.indexed_revision=report.revision
                existing_emb.indexed_at=now

        db.commit()
    except DomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {
        'report_id':report.report_id,
        'revision':report.revision,
        'index_status':report.index_status,
        'embedding_updated':embedding_updated,
    }


def delete_report(db:Session,rid:int)->None:
    report=db.get(Report,rid)
    if report is None:
        raise DomainError(404,'REPORT_NOT_FOUND','요청한 보고서를 찾을 수 없습니다.')
    try:
        db.delete(report)
        db.commit()
    except Exception:
        db.rollback()
        raise


def summarize_report(db:Session,rid:int,style:str='brief',max_sentences:int=3)->dict:
    report,category,detail=repo.get_report_and_detail(db,rid)
    summary=llm_client.summarize(
        detail.content,
        style=style,
        max_sentences=max_sentences,
        allow_structured=at_least('intermediate'),
    )
    return {
        'report_id':report.report_id,
        'source_revision':report.revision,
        'summary':summary,
        'model':settings.llm_model,
        'status':'completed',
    }

