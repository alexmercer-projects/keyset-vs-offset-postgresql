# Keyset vs OFFSET pagination (PostgreSQL)

A four-page unpublished write-up, with a benchmark script you can rerun. It measures how OFFSET pagination slows down on a 2-million-row table and shows the keyset (seek) query that replaces it, including two correctness traps.

Headline result from the attached run: skipping 1.9 million rows took 344.951 ms with OFFSET and 0.022 ms with keyset, on PostgreSQL 16.4.

This is an unpublished spec sample, not a client report and not an article that ran in a magazine. The numbers come from the attached script on one machine. Absolute times will differ if you rerun it, but the shape should hold.

## Read it

The write-up is `Keyset-vs-OFFSET-PostgreSQL-SAMPLE.pdf`.

## Run the benchmark

You need Python 3, `psycopg2`, and a scratch PostgreSQL 12+ database you don't mind wiping. The script drops and recreates an `orders` table.

```
export DSN="host=localhost port=5432 user=postgres dbname=postgres"
python3 keyset_bench.py
```

It writes `keyset_results.json` in the current directory.

## Tech

Python 3 and PostgreSQL. The only Python dependency is `psycopg2`.

## Contact

[alexmercer.projects@gmail.com](mailto:alexmercer.projects@gmail.com)
