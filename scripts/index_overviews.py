"""실제 E5-small로 초기 개요 180건을 Vector(384)에 일괄 적재합니다."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from datetime import datetime,timezone
from sqlalchemy import select
from app.db.session import get_session_factory
from app.models.report import Report,ReportEmbedding
from app.integrations import embedding_client as emb
from app.core.config import settings


def index_overviews():
    with get_session_factory()() as db:
        reports=db.execute(select(Report).order_by(Report.report_id.asc())).scalars().all()
        if not reports:
            print('색인할 보고서가 없습니다.')
            return 0
        batch_size=32
        now=datetime.now(timezone.utc)
        total=0
        for i in range(0,len(reports),batch_size):
            batch=reports[i:i+batch_size]
            vectors=emb.encode([r.overview for r in batch],kind='passage')
            for r,vec in zip(batch,vectors):
                ohash=emb.overview_hash(r.overview)
                existing=db.get(ReportEmbedding,r.report_id)
                if existing is None:
                    db.add(ReportEmbedding(
                        report_id=r.report_id,
                        embedding=vec,
                        model_name=settings.embedding_model,
                        overview_hash=ohash,
                        indexed_revision=r.revision,
                        indexed_at=now,
                    ))
                else:
                    existing.embedding=vec
                    existing.model_name=settings.embedding_model
                    existing.overview_hash=ohash
                    existing.indexed_revision=r.revision
                    existing.indexed_at=now
                r.index_status='ready'
                total+=1
        db.commit()
        print(f'초기 개요 일괄 색인 완료: {total}건 ({settings.embedding_model})')
        return total

def main():
    """실행 진입점: 실제 적재 절차는 index_overviews()에서 수행합니다."""
    index_overviews()

if __name__=='__main__':main()
