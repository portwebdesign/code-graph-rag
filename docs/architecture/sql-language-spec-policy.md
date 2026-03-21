# SQL Language Spec Policy

Tarih: 2026-03-21

Bu belge SQL parser modelinde callable ve schema artefakti ayrimini netlestirir.

## 1. Problem

SQL migration dosyalari (alembic/flyway/liquibase/migrations) icindeki DDL varliklar,
CALLS fallback veya reparse resolver tarafinda yanlislikla callable hedef gibi
degerlendirilebilir.

Bu durum production graph'ta false-positive CALLS edge'leri uretir.

## 2. Policy

1. CALLS resolver katmaninda yalniz callable node tipleri aday olabilir.
- Callable tipler: Function, Method.

2. SQL migration kaynakli semboller CALLS adayi olamaz.
- Kaynak dosya `.sql` olacak.
- Yol parcalarinda migration marker'larindan en az biri bulunacak:
  - migration, migrations, alembic, flyway, liquibase

3. SQL type tuple politikasinda view callable degildir.
- `create_view` class/schema ailesine tasinir.
- `create_procedure` function ailesine dahil edilir.

## 3. Beklenen Davranis

- Production fonksiyonlar migration DDL sembollerine CALLS edge yazmaz.
- SQL function/procedure/trigger gibi gercek callable tanimlar, migration disi contextte
  cozumlenebilir kalir.

## 4. Uygulama Noktalari

- codebase_rag/parsers/pipeline/call_resolver.py
- codebase_rag/parsers/pipeline/call_processor.py
- codebase_rag/parsers/pipeline/reparse_registry_resolver.py
- codebase_rag/core/constants.py

## 5. Dogrulama

- Unit: SQL type tuple policy testi
- Integration: SQL migration sembollerine CALLS yazilmadigini fixture ile dogrulama

Ornek smoke query:

```cypher
MATCH ()-[r:CALLS]->(b:Class)
WHERE b.project_name = $project_name
RETURN count(r) AS calls_to_class;
```

Beklenen: migration fixture icin `0`.
