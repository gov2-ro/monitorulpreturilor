# Useful SQLite Queries

Ad-hoc queries for exploring `data/prices.db`.

> Key queries are also available as **SQLite views** (created automatically by `init_db()`):
> `v_price_variability`, `v_cross_network_spread`, `v_product_popularity`,
> `v_private_label_candidates`, `v_stores_per_network`, `v_price_freshness`, `v_products_no_prices`
>
> ```bash
> sqlite3 data/prices.db "SELECT * FROM v_price_variability LIMIT 20;"
> python export_analytics.py   # → docs/data/*.csv
> ```

---

## Price variability

### Intra-network spread — products with price differences across stores in the same network

```sql
SELECT n.name        AS network,
       p.product_id,
       pr.name       AS product,
       COUNT(DISTINCT p.store_id)               AS stores,
       MIN(p.price)                             AS min_price,
       MAX(p.price)                             AS max_price,
       ROUND(MAX(p.price) - MIN(p.price), 2)   AS spread
FROM prices p
JOIN stores           s  ON p.store_id   = s.id
JOIN retail_networks  n  ON s.network_id = n.id
JOIN products         pr ON p.product_id = pr.id
GROUP BY n.name, p.product_id
HAVING stores > 2 AND spread > 0
ORDER BY spread DESC
LIMIT 20;
```


Outlier-filtered version — prices more than 10× the group minimum are excluded before
aggregation (catches data-entry errors like bani-instead-of-RON). Filtered to latest date.
Adjust the `* 10` threshold as needed.

```sql
WITH floor AS (
  -- min price per product × network on the latest date
  SELECT pr.product_id, s.network_id,
         MIN(pr.price) AS min_p
  FROM prices pr
  JOIN stores s ON pr.store_id = s.id
  WHERE pr.price_date = (SELECT MAX(price_date) FROM prices)
    AND pr.price > 0
  GROUP BY pr.product_id, s.network_id
),
clean AS (
  -- drop any price that is more than 10× the group minimum
  SELECT pr.product_id, pr.store_id, pr.price, s.network_id
  FROM prices pr
  JOIN stores s ON pr.store_id = s.id
  JOIN floor  f ON pr.product_id = f.product_id AND s.network_id = f.network_id
  WHERE pr.price_date = (SELECT MAX(price_date) FROM prices)
    AND pr.price > 0
    AND pr.price <= f.min_p * 10
)
SELECT n.name                                                           AS network,
       c.product_id,
       p.name                                                           AS product,
       COUNT(DISTINCT c.store_id)                                      AS stores,
       ROUND(MIN(c.price), 2)                                          AS min_price,
       ROUND(AVG(c.price), 2)                                          AS avg_price,
       ROUND(MAX(c.price), 2)                                          AS max_price,
       ROUND((MAX(c.price) - MIN(c.price)) / MIN(c.price) * 100, 1)   AS spread_pct
FROM clean c
JOIN retail_networks n ON c.network_id = n.id
JOIN products        p ON c.product_id = p.id
GROUP BY n.name, c.product_id
HAVING stores >= 3 AND spread_pct > 5
ORDER BY spread_pct DESC
LIMIT 20;
```

### Cross-network median price per product (top gaps)

```sql
WITH per_store AS (
    SELECT p.product_id, pr.name AS product,
           n.name AS network, p.price
    FROM prices p
    JOIN stores          s  ON p.store_id   = s.id
    JOIN retail_networks n  ON s.network_id = n.id
    JOIN products        pr ON p.product_id = pr.id
    WHERE n.name != 'SELGROS'
),
net_median AS (
    -- SQLite has no MEDIAN; approximate with AVG of middle rows
    SELECT product_id, product, network,
           AVG(price) AS avg_price,
           COUNT(*)   AS stores
    FROM per_store
    GROUP BY product_id, network
    HAVING stores >= 2
),
product_range AS (
    SELECT product_id, product,
           MIN(avg_price) AS min_net_price,
           MAX(avg_price) AS max_net_price,
           COUNT(DISTINCT network) AS networks,
           ROUND(MAX(avg_price) - MIN(avg_price), 2) AS spread,
           ROUND(MAX(avg_price) / MIN(avg_price), 3) AS ratio
    FROM net_median
    GROUP BY product_id
    HAVING networks >= 2
)
SELECT * FROM product_range
ORDER BY ratio DESC
LIMIT 20;
```

---

## Store coverage

### Stores per network

```sql
SELECT COALESCE(n.name, 'Unknown') AS network,
       COUNT(*) AS stores
FROM stores s
LEFT JOIN retail_networks n ON s.network_id = n.id
GROUP BY network
ORDER BY stores DESC;
```

### Stores missing network_id

```sql
SELECT COUNT(*) AS stores_no_network FROM stores WHERE network_id IS NULL;
```

### Top stores by surrounding population

```sql
SELECT name, CAST(surrounding_population AS INTEGER) AS pop
FROM stores
ORDER BY surrounding_population DESC
LIMIT 20;
```

---

## Products

### Products appearing in only one network (private-label candidates)

```sql
SELECT pr.name, n.name AS network, COUNT(DISTINCT s.id) AS stores
FROM prices p
JOIN stores          s  ON p.store_id   = s.id
JOIN retail_networks n  ON s.network_id = n.id
JOIN products        pr ON p.product_id = pr.id
GROUP BY p.product_id
HAVING COUNT(DISTINCT s.network_id) = 1
ORDER BY stores DESC
LIMIT 30;
```

### Category product counts

```sql
SELECT c.name AS category, COUNT(*) AS products
FROM products p
JOIN categories c ON p.categ_id = c.id
GROUP BY c.name
ORDER BY products DESC
LIMIT 20;
```

---

## Run history

```sql
SELECT script, started_at, status,
       uats_processed AS items, records_written
FROM runs
ORDER BY started_at DESC
LIMIT 10;
```

---

## Product popularity

These queries require at least one completed price fetch to return meaningful results.
"Popularity" is a blended rank of **store coverage** (distinct stores stocking the product)
and **record count** (total price rows — weights larger stores more). Lower blended rank = more popular.

### Top 50 products overall

```sql
WITH coverage AS (
    SELECT product_id,
           COUNT(DISTINCT store_id) AS store_count,
           COUNT(*)                 AS record_count
    FROM prices
    GROUP BY product_id
),
ranked AS (
    SELECT product_id, store_count, record_count,
           RANK() OVER (ORDER BY store_count  DESC) AS cov_rank,
           RANK() OVER (ORDER BY record_count DESC) AS rec_rank
    FROM coverage
)
SELECT r.product_id,
       pr.name,
       r.store_count,
       r.record_count,
       ROUND((r.cov_rank + r.rec_rank) / 2.0, 1) AS blended_rank
FROM ranked r
JOIN products pr ON r.product_id = pr.id
ORDER BY blended_rank
LIMIT 50;
```

### Top 20 products per category

```sql
WITH coverage AS (
    SELECT p.product_id,
           pr.categ_id,
           COUNT(DISTINCT p.store_id) AS store_count,
           COUNT(*)                   AS record_count
    FROM prices p
    JOIN products pr ON p.product_id = pr.id
    GROUP BY p.product_id, pr.categ_id
),
ranked AS (
    SELECT *,
           RANK() OVER (
               PARTITION BY categ_id
               ORDER BY store_count DESC, record_count DESC
           ) AS rn
    FROM coverage
)
SELECT r.product_id,
       pr.name       AS product,
       c.name        AS category,
       r.store_count,
       r.record_count
FROM ranked r
JOIN products    pr ON r.product_id = pr.id
JOIN categories  c  ON r.categ_id   = c.id
WHERE r.rn <= 20
ORDER BY c.name, r.rn;
```

---

## CI store selection

Queries used by `build_ci_subset.py` to build the limited store set for GitHub Actions runs.

### Top 10 stores per network by surrounding population

```sql
WITH ranked AS (
    SELECT s.id,
           s.name,
           n.name                   AS network,
           s.surrounding_population,
           ROW_NUMBER() OVER (
               PARTITION BY s.network_id
               ORDER BY s.surrounding_population DESC
           ) AS rn
    FROM stores s
    JOIN retail_networks n ON s.network_id = n.id
    WHERE s.lat IS NOT NULL
      AND s.surrounding_population IS NOT NULL
)
SELECT id, name, network,
       CAST(surrounding_population AS INTEGER) AS pop
FROM ranked
WHERE rn <= 10
ORDER BY network, rn;
```

### Middle-population batch (percentile 35–65), geographically spread

SQLite has no `PERCENTILE_CONT`; use window-function offset arithmetic instead.
The Z-order sort spreads the selection across Romania so no region is over-represented.

```sql
WITH ordered AS (
    SELECT id, name, surrounding_population, lat, lon,
           ROW_NUMBER() OVER (ORDER BY surrounding_population) AS rn,
           COUNT(*)     OVER ()                                AS total
    FROM stores
    WHERE lat IS NOT NULL
      AND surrounding_population IS NOT NULL
),
middle AS (
    SELECT * FROM ordered
    WHERE rn BETWEEN CAST(total * 0.35 AS INTEGER)
                 AND CAST(total * 0.65 AS INTEGER)
)
SELECT id, name,
       CAST(surrounding_population AS INTEGER) AS pop,
       lat, lon
FROM middle
ORDER BY
    CAST((lat - 43.6) / 0.45 AS INTEGER),   -- Z-order row
    CAST((lon - 20.3) / 0.45 AS INTEGER)    -- Z-order col
LIMIT 50;
```

---

## Data quality

### Store coverage (stores with at least one price record)

```sql
SELECT
    (SELECT COUNT(DISTINCT store_id) FROM prices)  AS stores_with_prices,
    (SELECT COUNT(*)                FROM stores)   AS total_stores;
```

### Products with no price records

```sql
SELECT pr.id, pr.name, c.name AS category
FROM products pr
JOIN categories c ON pr.categ_id = c.id
WHERE pr.id NOT IN (SELECT DISTINCT product_id FROM prices)
ORDER BY c.name, pr.name
LIMIT 50;
```

### Price records per fetch date (freshness check)

```sql
SELECT price_date,
       COUNT(*)                  AS records,
       COUNT(DISTINCT store_id)  AS stores,
       COUNT(DISTINCT product_id) AS products
FROM prices
GROUP BY price_date
ORDER BY price_date DESC
LIMIT 14;
```

### Stores fetched today

```sql
SELECT s.name, n.name AS network,
       COUNT(*) AS price_rows
FROM prices p
JOIN stores          s ON p.store_id   = s.id
JOIN retail_networks n ON s.network_id = n.id
WHERE DATE(p.fetched_at) = DATE('now')
GROUP BY s.id
ORDER BY price_rows DESC;
```
