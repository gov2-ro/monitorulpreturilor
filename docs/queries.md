# Useful SQLite Queries

Ad-hoc queries for exploring `data/prices.db`.

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
