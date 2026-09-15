# CodeJudge SQL & DBMS Dataset v2

This dataset represents raw database exports from **CodeJudge**, an online coding-practice and evaluation platform used in a Computer Science department.

The dataset is designed for a SQL/DBMS assignment covering relational design, keys, normalization, SQL querying, data integrity auditing, safe modifications, transactions, and ACID reasoning.

## Folder Structure

```text
data/
DATA_DICTIONARY.md
load_sqlite_raw.py
README_DATASET.md
```

## Dataset Tables

```text
batches.csv
courses.csv
students.csv
enrollments.csv
problems.csv
test_cases.csv
contests.csv
contest_problems.csv
submissions.csv
test_results.csv
sessions.csv
attendance.csv
regrade_requests.csv
plagiarism_flags.csv
raw_student_import.csv
operation_requests.csv
```

## Important Notes for Students

- Treat the CSV files as raw platform exports.
- Some records may contain realistic inconsistencies or missing values.
- Importing directly into a strict schema with foreign keys may fail until records are cleaned or staged.
- A good workflow is to first import the CSVs into raw/staging tables, audit the data, then design a cleaner relational schema.
- Use actual table names, column names, and record IDs from the dataset in your explanations.
- Do not edit the original CSV files directly. Use staging tables or database copies for repair tasks.

## Optional SQLite Raw Loader

A helper script is included:

```bash
python load_sqlite_raw.py
```

This creates `codejudge_raw.db` and imports each CSV into a raw table with flexible text columns. This is only a convenience loader. You are still expected to design your own relational schema as required by the assignment.
