"""Hybrid Search: keyword 후보를 먼저 검토하고, 없다면 의미 검색의 임계값을 적용합니다."""
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from app.models.report import Report,ReportEmbedding,ReportCategory
from app.core.config import settings,at_least
from app.integrations import embedding_client as emb
from app.services.errors import DomainError


def safe_like(s:str)->str:
    return s.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')


def find_similar(db:Session,query:str,category_code:str|None=None,
                 keyword_limit:int=20,semantic_limit:int=20)->dict:
    try:
        qvec=emb.encode([query],kind='query')[0]
    except DomainError:
        raise
    except Exception:
        raise DomainError(503,'EMBEDDING_UNAVAILABLE','검색어 임베딩 생성에 실패했습니다.')

    qvec_literal='['+','.join(f'{x:.8f}' for x in qvec)+']'
    cat_filter='AND v.category_code = :category_code' if category_code else ''
    base_params={'model_name':settings.embedding_model,'qvec':qvec_literal}
    if category_code:
        base_params['category_code']=category_code

    kw_params={**base_params,'pattern':f'%{safe_like(query)}%','limit':keyword_limit}
    kw_rows=db.execute(text(f"""
        SELECT v.report_id, v.title, v.category_code, v.overview,
               GREATEST(0.0, LEAST(1.0, 1.0 - (e.embedding <=> CAST(:qvec AS vector)))) AS sim
        FROM report_read_view v
        JOIN report_embeddings e ON e.report_id = v.report_id
        WHERE v.index_status = 'ready'
          AND e.indexed_revision = v.revision
          AND e.model_name = :model_name
          {cat_filter}
          AND (
              v.title ILIKE :pattern ESCAPE '\\'
              OR v.overview ILIKE :pattern ESCAPE '\\'
              OR v.content ILIKE :pattern ESCAPE '\\'
          )
        ORDER BY sim DESC, v.report_id ASC
        LIMIT :limit
    """),kw_params).mappings().all()

    sem_params={**base_params,'limit':semantic_limit}
    sem_rows=db.execute(text(f"""
        SELECT v.report_id, v.title, v.category_code, v.overview,
               GREATEST(0.0, LEAST(1.0, 1.0 - (e.embedding <=> CAST(:qvec AS vector)))) AS sim
        FROM report_read_view v
        JOIN report_embeddings e ON e.report_id = v.report_id
        WHERE v.index_status = 'ready'
          AND e.indexed_revision = v.revision
          AND e.model_name = :model_name
          {cat_filter}
        ORDER BY (e.embedding <=> CAST(:qvec AS vector)) ASC, v.report_id ASC
        LIMIT :limit
    """),sem_params).mappings().all()

    candidates:dict[int,dict]={}
    for r in kw_rows:
        candidates[r['report_id']]={
            'report_id':r['report_id'],
            'title':r['title'],
            'category_code':r['category_code'],
            'overview':r['overview'],
            'keyword_match':True,
            'cosine_similarity':round(float(r['sim']),4),
        }
    for r in sem_rows:
        rid=r['report_id']
        if rid not in candidates:
            candidates[rid]={
                'report_id':rid,
                'title':r['title'],
                'category_code':r['category_code'],
                'overview':r['overview'],
                'keyword_match':False,
                'cosine_similarity':round(float(r['sim']),4),
            }

    kw_list=[c for c in candidates.values() if c['keyword_match']]
    if kw_list:
        # Keyword 일치 후보 간에는 모두 Keyword 매칭 조건을 충족하므로 낮은 report_id 우선 규칙을 적용
        kw_list.sort(key=lambda c:c['report_id'])
        best=kw_list[0]
        return {'status':'found','query':query,'best_match':best}

    sem_list=[c for c in candidates.values() if c['cosine_similarity']>=settings.search_min_similarity]
    if sem_list:
        sem_list.sort(key=lambda c:(-c['cosine_similarity'],c['report_id']))
        best=sem_list[0]
        return {'status':'found','query':query,'best_match':best}

    return {'status':'not_found','query':query,'best_match':None}
