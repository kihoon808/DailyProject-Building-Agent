"""E5-small Adapter. 사용 전 SentenceTransformers·모델 가중치 준비 필요."""
from functools import lru_cache
import hashlib
from app.core.config import settings

@lru_cache(maxsize=1)
def get_model():
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(1)
    return SentenceTransformer(settings.embedding_model,device='cpu')


def encode(texts:list[str],kind:str='passage')->list[list[float]]:
    if kind not in ('passage','query'):
        raise ValueError(f'unsupported embedding kind: {kind}')
    if not texts:
        return []
    prefixed=[f'{kind}: {t}' for t in texts]
    model=get_model()
    max_len=getattr(model,'max_seq_length',512)
    tokenizer=getattr(model,'tokenizer',None)
    if tokenizer is not None:
        for item in prefixed:
            ids=tokenizer(item,add_special_tokens=True,truncation=False)['input_ids']
            if len(ids)>max_len:
                raise ValueError(f'embedding input exceeds max_seq_length ({len(ids)} > {max_len})')
    vectors=model.encode(prefixed,normalize_embeddings=True)
    result=[]
    for vec in vectors:
        row=[float(x) for x in vec.tolist()]
        if len(row)!=384:
            raise RuntimeError(f'unexpected embedding dimension: {len(row)}')
        result.append(row)
    return result

def overview_hash(s:str)->str:
    # 해시 기준: 저장된 Overview의 UTF-8 원문. Prefix는 저장해시에서 제외합니다.
    return hashlib.sha256(s.encode('utf-8')).hexdigest()
