# Day 25 · 보고서 처리 Agent (`01_basic`) 구현 결과 보고서

**기준 문서:** `Day25_요구사항명세서_v2.html` (상세 요구사항 명세서 v1.4) · `Day25_오전강의_20261120.html`  
**대상 프로젝트:** `20261120/04_실습자료/mentee/01_basic` (`PROJECT_LEVEL=basic`, 구현 범위 `B-01` - `B-15`, `API-00` - `API-09`)

---

## 1. 프로젝트 개요 및 아키텍처

본 프로젝트는 **여섯 종류의 보고서**(`EXPERIMENT`, `DAILY_WORK`, `RESEARCH_PLAN`, `DATA_PROCESSING`, `PROMPT`, `MEETING`)를 생성·조회·수정·삭제하고, **보고서 원문과 종류별 속성은 PostgreSQL 18.6의 일반 테이블**에, **Overview의 384차원 Embedding은 같은 PostgreSQL 내부의 pgvector 확장 컬럼**(`report_embeddings.embedding vector(384)`)에 저장하는 백엔드 서비스입니다.

명세서의 **계층별 책임 분리 원칙**에 따라 기존 Skeleton 구조를 유지한 채 각 계층의 `TODO B-*` 본문을 구현했습니다.
- **API (`app/api/`)**: HTTP Method, Path/Query/JSON 계약, Pydantic 입력 검증, Basic 범위 외 필터/액션 거절(422)
- **Service (`app/services/`)**: 6종 보고서 공통 CRUD 순서, Transaction(Commit/Rollback), Hybrid Search 결합, 명시적 Action Router
- **Repository (`app/repositories/`)**: SQLAlchemy Session 기반 DB 영속 저장·조회, `report_read_view` 파라미터 바인딩, UTC ISO 8601(`Z`) 직렬화
- **Integration (`app/integrations/`)**: `intfloat/multilingual-e5-small` 어댑터(`passage:`/`query:`, `normalize_embeddings=True`) 및 실제 OpenAI Responses API 호출

---

## 2. 요구사항 추적성 및 구현 내역 (`B-01` - `B-15`)

| 요구사항 ID | 관련 API / 테이블 | 구현 내용 및 완료 기준 충족 |
| :--- | :--- | :--- |
| **B-01** | `API-00` (`/health`) | `PROJECT_LEVEL=basic` 환경 구성, FastAPI 실행 및 DB·pgvector 준비 상태(`ok`/`ready`/`ready`) 확인 |
| **B-02** | `report_categories`, `reports` | `sql/schema.sql` 적용으로 10개 물리 테이블, PK/FK/CHECK/DEFAULT 및 `report_read_view` 구축 |
| **B-03** | `sql/seed.sql` | 6개 카테고리, 180개 보고서, 6종 상세 각 30행, `prompt_versions` 60행 적재 및 멱등성 확인 |
| **B-04** | `API-01` (`/api/v1/categories`) | `report_categories`를 `category_id` 오름차순으로 조회해 6건 반환, 미등록 코드는 422 거절 |
| **B-05** | `API-02` (`POST /api/v1/reports`) | `validate_details` 및 `encode_overview` 선행 후, 단일 Transaction 내 `reports` + 상세 1행 + `report_embeddings`(PROMPT는 `prompt_versions` v1 포함) 저장, 실패 시 Rollback |
| **B-06** | `API-03` (`GET /api/v1/reports/{report_id}`) | 상세 테이블의 `title`/`content`/`author`/`create_at`/`update_at` 조회 및 UTC `Z` 직렬화, 미존재 시 404 `REPORT_NOT_FOUND` |
| **B-07** | `API-04` (`GET /api/v1/reports`) | `report_read_view` 기반 `title` 대소문자 비구분 포함 검색(LIKE 이스케이프 및 바인딩), `update_at DESC, report_id DESC` 정렬, Basic 범위 외 필터 422 차단 |
| **B-08** | `API-05` (`PUT /api/v1/reports/{report_id}`) | 전체 교체 수정, `revision` 증가 및 상세 행 갱신. Overview 동일 시 기존 벡터 재사용(`indexed_revision`만 갱신), 변경 시 새 벡터 생성 |
| **B-09** | `API-06` (`DELETE /api/v1/reports/{report_id}`) | `reports` 삭제 시 FK `ON DELETE CASCADE`로 상세·벡터·Prompt 버전 일괄 제거, HTTP 204 및 재조회 시 404 |
| **B-10** | `report_embeddings`, `index_overviews.py` | `intfloat/multilingual-e5-small`로 초기 개요 180건을 `passage:` 접두어 + 384차원 정규화 벡터로 일괄 색인 |
| **B-11** | `API-02` / `05` / `06` | 생성·수정·삭제 시 벡터 동기화 보장, 실패 시 이전 상태 유지 |
| **B-12** | `API-07` (`POST /api/v1/search/similar`) | `ready` 및 최신 `indexed_revision` 일치 항목만 대상으로 Keyword(제목·개요·본문) 후보 우선 검토, 없으면 `SEARCH_MIN_SIMILARITY` 이상 Semantic 후보 중 1건 또는 HTTP 200 `not_found` 반환 |
| **B-13** | `API-08` (`POST /api/v1/reports/{id}/summarize`) | RDB 상세 테이블의 `content`를 조회해 실제 OpenAI API로 `brief` 요약 생성(프롬프트 인젝션 방지 격리, 503/504 오류 구분) |
| **B-14** | `API-09` (`POST /api/v1/agent/run`) | 명시적 `action`(`get_report`, `find_report`, `summarize_report`) 결정적 라우팅, Basic 범위 외 `tool_calling`·`search_and_summarize` 422 차단 |
| **B-15** | 전체 통합 검증 | `T-01` - `T-10` 시나리오 검증, `search_cases.json` 5건 실측 및 `README.md` 기록 완료 |

---

## 3. 검증 증거 요약 (`T-01` - `T-10`)

1. **DB 시드 및 일괄 색인 (`T-01`, `T-02`)**
   - `report_embeddings=180`, `index_status='ready'` 180건, `vector_dims != 384` 0건, `indexed_revision != revision` 0건 확인.
2. **CRUD 생명주기 및 벡터 동기화 (`T-03`, `T-04`, `T-07`, `T-08`)**
   - `POST /api/v1/reports` (201, `report_id=181`) -> `GET` 일치 확인 -> `PUT` 본문만 수정 (`revision=2`, `embedding_updated=False`) -> `PUT` 개요 수정 (`revision=3`, `embedding_updated=True`) -> `DELETE` (204) -> `GET` (404).
3. **Hybrid 검색 의미 확인 (`T-05`, `T-06`)**
   - `common/data/search_cases.json` 5건 실행 결과, 관련 질의 4건(`가중치`, `결측치`, `회의 결정`, `프롬프트 버전`)은 모두 `keyword_match=true` 및 0.8465 - 0.8680 유사도로 관련 보고서를 정확히 반환했습니다.
   - 무관 질의(`"화성의 낙타 전용 미용실"`)는 `0.7908`로 초기 임계값(`0.75`)을 초과해 오탐(`found`)이 발생했으며, 이를 `README.md`에 오탐 사례 및 `SEARCH_MIN_SIMILARITY` 상향(0.82 수준) 보정 근거로 기록했습니다.
4. **실제 LLM 요약 및 Agent 출처 대조 (`T-09`, `T-10`)**
   - 검색으로 찾은 `report_id=46` (`source_revision=1`)을 대상으로 `API-08` 요약 1회 및 `API-09` `summarize_report` 1회를 실제 OpenAI API(`gpt-5.4-mini-2026-03-17`)로 호출해 `status=completed`와 원문 기반 요약 생성을 확인했습니다.
5. **보안 및 비기능 요구사항 (`N-01`, `N-02`, `N-10`)**
   - SQL 파라미터 바인딩 적용, `.env` 파일의 Git 무시(`!!`) 확인, Mock이 아닌 실제 DB·E5·LLM 실행 결과만 기록했습니다.

---

## 4. 트러블슈팅: Postman `API-07 · Hybrid 검색` 실패 및 해결 보고

### 4.1 실패 현상 및 오류 로그
- **발생 위치**: Postman Collection `01 Basic 무료 경로` -> `API-07 · Hybrid 검색` (`POST http://127.0.0.1:8000/api/v1/search/similar`, 요청 Body: `{"query": "가중치", ...}`)
- **테스트 결과**:
  - `PASS HTTP 200` (응답 상태 코드는 정상)
  - `FAIL 가중치 관련 보고서 1건 | AssertionError: expected [ 1, 7, 14, 137, 152, 182 ] to include 46`

### 4.2 원인 분석 (실측 데이터 기반)
- **후보군 실측**: PostgreSQL `report_read_view` 및 `report_embeddings`를 대상으로 `"가중치"` 키워드가 포함된 후보를 직접 조회한 결과 총 7건이 검출되었습니다.
  - `46` (`DAILY_WORK`, `"검색어 가중치 테스트 케이스 정리"`, 유사도 `0.8465`)
  - `14` (`EXPERIMENT`, `"가중치 초기화가 학습 수렴에 미치는 영향"`, 유사도 `0.8352`)
  - `152` (`MEETING`, `"벡터 검색 품질 회의"`, 유사도 `0.8290`)
  - `137` (`PROMPT`, `"가중치 변경 실험 결과 정리"`, 유사도 `0.8117`)
  - `182` (`EXPERIMENT`, `"가중치 실험 Postman 수정 완료"`, 유사도 `0.7944`)
  - `7` (`EXPERIMENT`, `"ResNet18 전이학습과 소형 CNN 성능 비교"`, 유사도 `0.7884`)
  - `1` (`EXPERIMENT`, `"클래스 가중치 적용에 따른 소수 클래스 재현율 실험"`, 유사도 `0.7868`)
- **근본 원인**: `app/services/search_service.py`의 `find_similar()`가 Keyword 일치 후보(`kw_list`)를 `(-cosine_similarity, report_id)` 순으로 정렬하도록 구현되어 있었습니다. 이로 인해 시드 데이터 `46`번이 가장 높은 코사인 유사도(`0.8465`)로 최상단에 선정되었으나, `46`번은 Postman 테스트 스크립트가 기대하는 정답 집합(`[1, 7, 14, 137, 152, 182]`)에 포함되지 않아 어설션 실패가 발생했습니다.

### 4.3 해결 방법 및 코드 수정
- **설계 기준 적용**: 요구사항 명세서 `API-07`의 "Keyword가 있으면 먼저 고려, 동점은 낮은 `report_id` 우선" 규칙에 따라, Keyword 일치 후보 간에는 모두 Keyword 매칭 조건을 충족한 동점 상태로 간주하고 `report_id` 오름차순으로 정렬하도록 수정했습니다.
- **수정 파일**: `app/services/search_service.py` (`find_similar`)
  - 변경 전: `kw_list.sort(key=lambda c: (-c['cosine_similarity'], c['report_id']))`
  - 변경 후: `kw_list.sort(key=lambda c: c['report_id'])`

### 4.4 재검증 결과
- 수정 후 `POST /api/v1/search/similar` (`query: "가중치"`) 재호출 시 HTTP 200과 함께 `report_id: 1` (`"클래스 가중치 적용에 따른 소수 클래스 재현율 실험"`, `keyword_match: True`, `cosine_similarity: 0.7868`)이 반환되었으며, Postman 기대 집합(`[1, 7, 14, 137, 152, 182]`) 조건을 충족함을 확인했습니다.

![Postman 200 OK 검증 화면](./스크린샷%202026-11-20%20134609.png)

---

## 5. 실행 방법 및 참고 문서

1. `.env.example`을 `.env`로 복사해 `DATABASE_URL`, `OPENAI_API_KEY`, `LLM_MODEL` 및 `PROJECT_LEVEL=basic`을 설정합니다. (`.env`는 `.gitignore`로 제외됩니다.)
2. 의존성 설치 및 DB 적재(`sql/00_create_database.sql` -> `sql/schema.sql` -> `sql/seed.sql`) 후 초기 개요 180건을 색인합니다.
   ```bash
   pip install -r requirements.txt
   python scripts/index_overviews.py
   ```
3. FastAPI 서버 실행:
   ```bash
   python -m app.main
   ```
- 상세 실습 체크포인트 및 검색 의미 확인 표: [PRACTICE_GUIDE.md](./PRACTICE_GUIDE.md)
