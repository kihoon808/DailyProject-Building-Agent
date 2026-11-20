"""Repository: 영속 저장·조회만 담당. 외부 API/Embedding 요청은 여기서 하지 않습니다."""
from datetime import date,datetime,timezone
from typing import Any
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from app.models.report import (ReportCategory,Report,ReportEmbedding,DETAIL_MODELS,DETAIL_FIELDS,
                               PromptVersion)
from app.services.errors import DomainError


def category_for_code(db:Session,code:str)->ReportCategory:
    cat=db.execute(select(ReportCategory).where(ReportCategory.code==code)).scalar_one_or_none()
    if cat is None:
        raise DomainError(422,'VALIDATION_ERROR','지원하지 않는 카테고리 코드입니다.')
    return cat

def get_report_and_detail(db:Session,rid:int,lock:bool=False):
    stmt=select(Report,ReportCategory).join(
        ReportCategory,Report.category_id==ReportCategory.category_id
    ).where(Report.report_id==rid)
    if lock:
        stmt=stmt.with_for_update()
    row=db.execute(stmt).first()
    if row is None:
        raise DomainError(404,'REPORT_NOT_FOUND','요청한 보고서를 찾을 수 없습니다.')
    report,category=row
    detail_cls=DETAIL_MODELS.get(category.code)
    if detail_cls is None:
        raise DomainError(500,'DATA_INTEGRITY_ERROR','보고서 카테고리 매핑을 확인할 수 없습니다.')
    d_stmt=select(detail_cls).where(detail_cls.report_id==rid)
    if lock:
        d_stmt=d_stmt.with_for_update()
    detail=db.execute(d_stmt).scalar_one_or_none()
    if detail is None:
        raise DomainError(500,'DATA_INTEGRITY_ERROR','보고서 상세 행이 누락되었습니다.')
    return report,category,detail

def plain_value(obj:Any):
    if isinstance(obj,datetime):
        if obj.tzinfo is None:
            raise DomainError(500,'DATA_INTEGRITY_ERROR','시간대 정보가 없는 시각 데이터입니다.')
        return obj.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    if isinstance(obj,date):
        return obj.isoformat()
    return obj

def serialize_report(report:Report,category:ReportCategory,detail)->dict:
    details={field:plain_value(getattr(detail,field)) for field in DETAIL_FIELDS[category.code]}
    return {
        'report_id':report.report_id,
        'category_code':category.code,
        'title':detail.title,
        'content':detail.content,
        'author':detail.author,
        'overview':report.overview,
        'details':details,
        'revision':report.revision,
        'index_status':report.index_status,
        'created_at':plain_value(detail.create_at),
        'updated_at':plain_value(detail.update_at),
    }

def read_report(db:Session,rid:int)->dict:
    report,category,detail=get_report_and_detail(db,rid)
    return serialize_report(report,category,detail)


def list_reports(db:Session, title:str|None,limit:int,offset:int,
                 category_code:str|None=None,updated_after:datetime|None=None)->dict:
    conds=[]
    params:dict[str,Any]={'limit':limit,'offset':offset}
    if title is not None and title.strip():
        escaped=title.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        conds.append("title ILIKE :title_pattern ESCAPE '\\'")
        params['title_pattern']=f'%{escaped}%'
    if category_code is not None:
        conds.append('category_code = :category_code')
        params['category_code']=category_code
    if updated_after is not None:
        conds.append('update_at >= :updated_after')
        params['updated_after']=updated_after
    where_sql=('WHERE '+' AND '.join(conds)) if conds else ''
    total=db.execute(text(f'SELECT COUNT(*) FROM report_read_view {where_sql}'),params).scalar_one()
    rows=db.execute(text(
        f'SELECT report_id, category_code, title, revision '
        f'FROM report_read_view {where_sql} '
        f'ORDER BY update_at DESC, report_id DESC '
        f'LIMIT :limit OFFSET :offset'
    ),params).mappings().all()
    items=[{'report_id':r['report_id'],'category_code':r['category_code'],
            'title':r['title'],'revision':r['revision']} for r in rows]
    return {'items':items,'total':int(total),'limit':limit,'offset':offset}
